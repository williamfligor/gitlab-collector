#!/usr/bin/python

import json
import datetime
import re
import sys
import time
import urllib.request as urllib2
import gitlab
import configparser
import fnmatch

from prometheus_client import start_http_server
from prometheus_client.core import GaugeMetricFamily, REGISTRY

epoch = datetime.datetime.utcfromtimestamp(0)

class GitlabCollector(object):
    pipeline_status_map = {
        'running': 0,
        'pending': 1,
        'success': 2,
        'failed': 3,
        'canceled': 4,
        'skipped': 5
    }

    issue_status_map = {
        'opened': 0,
        'closed': 1,
    }

    mr_status_map = {
        'opened': 0,
        'closed': 1,
        'locked': 2,
        'merged': 3,
    }

    def __init__(self):
        self.projects = []
        self.gl = gitlab.Gitlab.from_config('gitlab', ['config.cfg'])

        config = configparser.ConfigParser()
        config.read('config.cfg')

        self.filters = []

        if 'filter' in config:
            filter_config = config['filter']
            if 'filter' not in filter_config:
                raise Exception('Filter config missing key "filter"')

            filters = filter_config['filter']
            filter_json = json.loads(filters)

            for filt in filter_json:
                self.filters.append(filt)
        
        self.load_projects()

        print('Exporting for these projects:')
        for proj in self.projects:
            print('    {}'.format(proj.path_with_namespace))
        print('')

    def load_projects(self):
        projects = self.gl.projects.list(membership=True, as_list=False)
        for project in projects:
            include_project = True

            if len(self.filters) > 0:
                include_project = False

                for project_filter in self.filters:
                    if fnmatch.fnmatch(project.path_with_namespace, project_filter):
                        include_project = True
                        break

            if include_project:
                self.projects.append(project)

    def collect(self):
        metrics = []

        metrics += self.collect_issues()
        metrics += self.collect_merge_requests()
        metrics += self.collect_pipelines()

        for metric in metrics:
            yield metric

    def collect_pipelines(self):
        pipeline_labels = [
            'project',
            'ref',
            'user',
            'username',
        ]

        c_status = GaugeMetricFamily('gitlab_pipeline_status2', 'status help text', labels=pipeline_labels)
        c_duration = GaugeMetricFamily('gitlab_pipeline_duration', 'status help text', labels=pipeline_labels)

        for proj in self.projects:
            for pipeline_short in proj.pipelines.list(as_list=False):
                pipeline = proj.pipelines.get(pipeline_short.id)

                labels = [
                    proj.path_with_namespace,
                    pipeline.ref,
                    pipeline.user['name'],
                    pipeline.user['username'],
                ]

                c_status.add_metric(labels=labels, value=self.pipeline_status_map[pipeline.status])
                c_duration.add_metric(labels=labels, value=pipeline.duration)

        return [c_status, c_duration]

    def collect_issues(self):
        issue_labels = [
            'project',
            'id',
            'title',
            'assigned_user',
            'assigned_username'
        ]

        c_status = GaugeMetricFamily('gitlab_issue_state', 'status help text', labels=issue_labels)
        c_created_at = GaugeMetricFamily('gitlab_issue_created_at', 'status help text', labels=issue_labels)

        for proj in self.projects:
            for issue in proj.issues.list(as_list=False):

                assigned_user = 'None'
                assigned_username = 'None'

                if len(issue.assignees) > 0:
                    assigned_user = issue.assignees[0]['name']
                    assigned_username = issue.assignees[0]['username']

                labels = [ 
                    proj.path_with_namespace,
                    str(issue.id),
                    issue.title,
                    assigned_user,
                    assigned_username,
                ]

                c_status.add_metric(labels=labels, value=self.issue_status_map[issue.state])
                c_created_at.add_metric(labels=labels, value=self.to_timestamp(issue.created_at))

        return [c_status, c_created_at]

    def collect_merge_requests(self):
        mr_labels = [
            'project',
            'id',
            'wip',
            'title',
            'assigned_user',
            'assigned_username'
        ]

        c_status = GaugeMetricFamily('gitlab_merge_request_state', 'status help text', labels=mr_labels)
        c_created_at = GaugeMetricFamily('gitlab_merge_request_created_at', 'status help text', labels=mr_labels)

        for proj in self.projects:
            for mr in proj.mergerequests.list(as_list=False):
                print(mr)

                assigned_user = 'None'
                assigned_username = 'None'

                if mr.assignee is not None:
                    assigned_user = mr.assignee['name']
                    assigned_username = mr.assignee['username']

                labels = [ 
                    proj.path_with_namespace,
                    str(mr.id),
                    str(mr.work_in_progress),
                    mr.title,
                    assigned_user,
                    assigned_username,
                ]

                c_status.add_metric(labels=labels, value=self.mr_status_map[mr.state])
                c_created_at.add_metric(labels=labels, value=self.to_timestamp(mr.created_at))

        return [c_status, c_created_at]

    def to_timestamp(self, date):
        date = date.replace("Z", "+00:00")
        date = datetime.datetime.fromisoformat(date)
        date = date.replace(tzinfo=datetime.timezone.utc).timestamp() * 1000

        return date


if __name__ == "__main__":
    REGISTRY.register(GitlabCollector())
    start_http_server(9118)
    while True:
        time.sleep(30)

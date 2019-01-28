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

from prometheus_client import start_http_server, CollectorRegistry
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
        self.groups = []
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
        
        self.load_groups()
        self.load_projects()

        self.collectors = []

    def load_groups(self):
        groups = self.gl.groups.list(as_list=False)
        for group in groups:
            include_group = True

            if len(self.filters) > 0:
                include_group = False

                for group_filter in self.filters:
                    if fnmatch.fnmatch(group.full_path, group_filter):
                        include_group = True
                        break

            if include_group:
                self.groups.append(group)

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

        print('Collecting...')

        for collector in self.collectors:
            metrics += collector()

        for metric in metrics:
            yield metric

    def collect_pipelines(self):
        pipeline_labels = [
            'project',
            'ref',
            'user',
            'username',
        ]

        c_status = GaugeMetricFamily('gitlab_pipeline_status', 'Pipeline status', labels=pipeline_labels)
        c_duration = GaugeMetricFamily('gitlab_pipeline_duration', 'Pipeline duration', labels=pipeline_labels)
        c_created_at = GaugeMetricFamily('gitlab_pipeline_created_at', 'Pipeline created_at', labels=pipeline_labels)

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
                c_created_at.add_metric(labels=labels, value=self.to_timestamp(pipeline.created_at))

        return [c_status, c_duration, c_created_at]

    def collect_issues(self):
        issue_labels = [
            'project',
            'id',
            'title',
            'assigned_user',
            'assigned_username'
        ]

        c_status = GaugeMetricFamily('gitlab_issue_state', 'Issue state', labels=issue_labels)
        c_created_at = GaugeMetricFamily('gitlab_issue_created_at', 'Issue created_at', labels=issue_labels)

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

        c_status = GaugeMetricFamily('gitlab_merge_request_state', 'Merge request state', labels=mr_labels)
        c_created_at = GaugeMetricFamily('gitlab_merge_request_created_at', 'Merge request created_at', labels=mr_labels)
        c_updated_at = GaugeMetricFamily('gitlab_merge_request_updated_at', 'Merge request updated_at', labels=mr_labels)

        for proj in self.projects:
            for mr in proj.mergerequests.list(as_list=False):
                assigned_user = 'None'
                assigned_username = 'None'

                if mr.assignee is not None:
                    assigned_user = mr.assignee['name']
                    assigned_username = mr.assignee['username']

                labels = [ 
                    proj.path_with_namespace,
                    str(mr.id),
                    str(mr.work_in_progress),
                    str(mr.title),
                    str(assigned_user),
                    str(assigned_username),
                ]

                c_status.add_metric(labels=labels, value=self.mr_status_map[mr.state])
                c_created_at.add_metric(labels=labels, value=self.to_timestamp(mr.created_at))
                c_updated_at.add_metric(labels=labels, value=self.to_timestamp(mr.updated_at))

        return [c_status, c_created_at, c_updated_at]

    def collect_membership(self):
        membership_labels = [
            'path',
            'user',
            'username',
        ]

        c_membership = GaugeMetricFamily('gitlab_membership', 'Membership', labels=membership_labels)

        for group in self.groups:
            for member in group.members.list(as_list=False):
                labels = [
                    group.full_path,
                    member.name,
                    member.username,
                ]

                c_membership.add_metric(labels=labels, value=member.access_level)

        for proj in self.projects:
            for member in proj.members.list(as_list=False):
                labels = [
                    proj.path_with_namespace,
                    member.name,
                    member.username,
                ]

                c_membership.add_metric(labels=labels, value=member.access_level)

        return [c_membership]

    def collect_paths(self):
        membership_labels = [
            'path',
        ]

        c_path = GaugeMetricFamily('gitlab_path', 'Paths', labels=membership_labels)

        for group in self.groups:
            labels = [
                group.full_path,
            ]

            c_path.add_metric(labels=labels, value=1)

        for proj in self.projects:
            labels = [
                proj.path_with_namespace,
            ]

            c_path.add_metric(labels=labels, value=1)

        return [c_path]

    def collect_protected_branches(self):
        pb_labels = [
            'project',
            'ref',
        ]

        c_push = GaugeMetricFamily('gitlab_protected_branch_push', 'Protected Branch', labels=pb_labels)
        c_merge = GaugeMetricFamily('gitlab_protected_branch_merge', 'Protected Branch', labels=pb_labels)

        for proj in self.projects:
            for branch in proj.branches.list(as_list=False):
                push = 0
                merge = 0

                if branch.protected:
                    # Default to super high level
                    push = 100000
                    merge = 100000

                    protection = proj.protectedbranches.get(branch.name)

                    # find lower access levels
                    for push_access in protection.push_access_levels:
                        push = min(push, push_access['access_level'])

                    # find lower access levels
                    for merge_access in protection.merge_access_levels:
                        merge = min(merge, merge_access['access_level'])

                labels = [
                    proj.path_with_namespace,
                    branch.name,
                ]

                c_push.add_metric(labels=labels, value=push)
                c_merge.add_metric(labels=labels, value=merge)

        return [c_push, c_merge]


    def to_timestamp(self, date):
        date = date.replace("Z", "+00:00")
        date = datetime.datetime.fromisoformat(date)
        date = date.replace(tzinfo=datetime.timezone.utc).timestamp() * 1000

        return date


if __name__ == "__main__":
    fast_register = CollectorRegistry(auto_describe=True)
    slow_register = CollectorRegistry(auto_describe=True)

    collector_1 = GitlabCollector()
    collector_2 = GitlabCollector()

    collector_1.collectors = [
        collector_1.collect_issues,
        collector_1.collect_merge_requests,
        collector_1.collect_pipelines,
    ]

    collector_2.collectors = [
        collector_2.collect_membership,
        collector_2.collect_paths,
        collector_2.collect_protected_branches,
    ]

    fast_register.register(collector_1)
    slow_register.register(collector_2)

    start_http_server(9118, registry=fast_register)
    start_http_server(9119, registry=slow_register)

    print('Started...')

    while True:
        time.sleep(30)

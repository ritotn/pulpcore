# -*- coding: utf-8 -*-
#
# Copyright © 2013 Red Hat, Inc.
#
# This software is licensed to you under the GNU General Public License as
# published by the Free Software Foundation; either version 2 of the License
# (GPLv2) or (at your option) any later version.
# There is NO WARRANTY for this software, express or implied, including the
# implied warranties of MERCHANTABILITY, NON-INFRINGEMENT, or FITNESS FOR A
# PARTICULAR PURPOSE.
# You should have received a copy of GPLv2 along with this software; if not,
# see http://www.gnu.org/licenses/old-licenses/gpl-2.0.txt

import os
from gettext import gettext as _

from pulp.bindings.exceptions import NotFoundException
from pulp.client.commands.options import DESC_ID, OPTION_CONSUMER_ID, OPTION_REPO_ID
from pulp.client.commands.polling import PollingCommand
from pulp.client.consumer_utils import load_consumer_id
from pulp.client.extensions.extensions import PulpCliFlag, PulpCliOption
from pulp.common import tags


OPTION_DISTRIBUTOR_ID = PulpCliOption('--distributor-id', DESC_ID, required=True)

FLAG_FORCE = PulpCliFlag('--force',
                         _('delete the binding immediately without tracking the progress'))


# consumer bindings management commands ----------------------------------------

class ConsumerBindCommand(PollingCommand):
    """
    Base class that binds a consumer to a repository and an arbitrary
    distributor.
    """

    def __init__(self, context, name=None, description=None):
        name = name or 'bind'
        description = description or _('binds a consumer to a repository')
        PollingCommand.__init__(self, name, description, self.run, context)

        self.add_option(OPTION_REPO_ID)
        self.add_consumer_option()
        self.add_distributor_option()

    def add_consumer_option(self):
        """
        Override this method to a no-op to skip adding the consumer id option.
        This allows commands (such as the consumer command) to find the consumer
        id via other means than a command line option.
        """
        self.add_option(OPTION_CONSUMER_ID)

    def add_distributor_option(self):
        """
        Override this method to a no-op to skip adding the distributor options.
        This allows derived commands to specialize (read: hard-code) the
        distributor types they work with.
        """
        self.add_option(OPTION_DISTRIBUTOR_ID)

    def run(self, **kwargs):
        consumer_id = self.get_consumer_id(kwargs)
        repo_id = kwargs[OPTION_REPO_ID.keyword]
        distributor_id = self.get_distributor_id(kwargs)

        response = self.context.server.bind.bind(consumer_id, repo_id, distributor_id)
        tasks = response.response_body  # already a list for bind
        self.poll(tasks)

    def get_consumer_id(self, kwargs):
        """
        Override this method to provide the consumer id to the run method.
        """
        return kwargs.get(OPTION_CONSUMER_ID.keyword, load_consumer_id(self.context))

    def get_distributor_id(self, kwargs):
        """
        Override this method to provide the distributor id to the run method.
        """
        return kwargs[OPTION_DISTRIBUTOR_ID.keyword]

    def task_header(self, task):

        handlers = {
            tags.action_tag(tags.ACTION_BIND) : self._render_bind_header,
            tags.action_tag(tags.ACTION_AGENT_BIND) : self._render_agent_bind_header,
        }

        # There will be exactly 1 action tag for each task (multiple resource tags)
        action_tags = [t for t in task.tags if tags.is_action_tag(t)]
        action_tag = action_tags[0]

        handler = handlers[action_tag]
        handler(task)

    def _render_bind_header(self, task):
        """
        Displays the task header for the bind task.
        """
        self.prompt.write('-- Updating Pulp Server --', tag='bind-header')

    def _render_agent_bind_header(self, task):
        """
        Displays the task header for the agent's bind task.
        """
        self.prompt.write('-- Notifying the Consumer --', tag='agent-bind-header')


class ConsumerUnbindCommand(PollingCommand):
    """
    Base class that unbinds a consumer from a repository and an arbitrary
    distributor.
    """

    def __init__(self, context, name=None, description=None):
        name = name or 'unbind'
        description = description or _('removes the binding between a consumer and a repository')
        PollingCommand.__init__(self, name, description, self.run, context)

        self.add_option(OPTION_REPO_ID)
        self.add_consumer_option()
        self.add_distributor_option()

        self.add_flag(FLAG_FORCE)

    def add_consumer_option(self):
        """
        Override this method to a no-op to skip adding the consumer id option.
        This allows commands (such as the consumer command) to find the consumer
        id via other means than a command line option.
        """
        self.add_option(OPTION_CONSUMER_ID)

    def add_distributor_option(self):
        """
        Override this method to a no-op to skip adding the distributor options.
        This allows derived commands to specialize (read: hard-code) the
        distributor types they work with.
        """
        self.add_option(OPTION_DISTRIBUTOR_ID)

    def run(self, **kwargs):
        consumer_id = self.get_consumer_id(kwargs)
        repo_id = kwargs[OPTION_REPO_ID.keyword]
        distributor_id = self.get_distributor_id(kwargs)
        force = kwargs[FLAG_FORCE.keyword]

        response = self.context.server.bind.unbind(consumer_id, repo_id, distributor_id, force)
        tasks = response.response_body  # already a list of tasks from the server
        self.poll(tasks)

    def get_consumer_id(self, kwargs):
        """
        Override this method to provide the consumer id to the run method.
        """
        return kwargs.get(OPTION_CONSUMER_ID.keyword, load_consumer_id(self.context))

    def get_distributor_id(self, kwargs):
        """
        Override this method to provide the distributor id to the run method.
        """
        return kwargs[OPTION_DISTRIBUTOR_ID.keyword]

    def task_header(self, task):

        handlers = {
            tags.action_tag(tags.ACTION_UNBIND) : self._render_unbind_header,
            tags.action_tag(tags.ACTION_AGENT_UNBIND) : self._render_agent_unbind_header,
            tags.action_tag(tags.ACTION_DELETE_BINDING) : self._render_delete_binding_header,
        }

        # There will be exactly 1 action tag for each task (multiple resource tags)
        action_tags = [t for t in task.tags if tags.is_action_tag(t)]
        action_tag = action_tags[0]

        handler = handlers[action_tag]
        handler(task)

    def _render_unbind_header(self, task):
        """
        Displays the task header for the unbind task.
        """
        self.prompt.write('-- Updating Pulp Server --', tag='unbind-header')

    def _render_agent_unbind_header(self, task):
        """
        Displays the task header for the agent's unbind task.
        """
        self.prompt.write('-- Notifying the Consumer --', tag='agent-unbind-header')

    def _render_delete_binding_header(self, task):
        """
        Displays the task header for the second update to the server's database.
        """
        self.prompt.write('-- Pulp Server Clean Up --', tag='delete-header')

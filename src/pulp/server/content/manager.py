# -*- coding: utf-8 -*-
#
# Copyright © 2011 Red Hat, Inc.
#
# This software is licensed to you under the GNU General Public
# License as published by the Free Software Foundation; either version
# 2 of the License (GPLv2) or (at your option) any later version.
# There is NO WARRANTY for this software, express or implied,
# including the implied warranties of MERCHANTABILITY,
# NON-INFRINGEMENT, or FITNESS FOR A PARTICULAR PURPOSE. You should
# have received a copy of GPLv2 along with this software; if not, see
# http://www.gnu.org/licenses/old-licenses/gpl-2.0.txt.

import copy
import itertools
import logging
import os
import re
from ConfigParser import SafeConfigParser
from gettext import gettext as _

from pulp.server import config
from pulp.server.content.distributor.base import Distributor
from pulp.server.content.exception import (
    ConflictingPluginError, MalformedPluginError, PluginNotFoundError)
from pulp.server.content.importer.base import Importer
from pulp.server.content.module import import_module

# globals ----------------------------------------------------------------------

_log = logging.getLogger(__name__)

_manager = None # Manager instance

# initial plugin and configuration file conventions

_top_level_configs_dir = '/etc/pulp'
_importer_configs_dir = os.path.join(_top_level_configs_dir, 'importers')
_distributor_configs_dir = os.path.join(_top_level_configs_dir, 'distributors')

_top_level_plugins_dir = os.path.dirname(__file__)
_importer_plugins_dir = os.path.join(_top_level_plugins_dir, 'importers')
_distributor_plugins_dir = os.path.join(_top_level_plugins_dir, 'distributors')

_top_level_plugins_package = 'pulp.server.content'
_importer_plugins_package = '.'.join((_top_level_plugins_package, 'importers'))
_distributor_plugins_package = '.'.join((_top_level_plugins_package, 'distributors'))

# manager class ----------------------------------------------------------------

class Manager(object):
    """
    Plugin manager class that discovers and associates importer and distributor
    plugin with content types.
    """
    def __init__(self):
        self.importer_config_paths = []
        self.importer_plugin_paths = {}
        self.distributor_config_paths = []
        self.distributor_plugin_paths = {}

        self.importer_configs = {}
        self.importer_plugins = {}
        self.distributor_configs = {}
        self.distributor_plugins = {}

    # plugin discovery configuration

    def _check_path(self, path):
        """
        Check a path for existence and read permissions.
        @type path: str
        @param path: file system path to check
        @raise ValueError: if path does not exist or is unreadable
        """
        if os.access(path, os.F_OK | os.R_OK):
            return
        raise ValueError(_('Cannot find path %s') % path)

    def add_importer_config_path(self, path):
        """
        Add a directory for importer configuration files.
        @type path: str
        @param path: importer configuration directory
        """
        self._check_path(path)
        self.importer_config_paths.append(path)

    def add_importer_plugin_path(self, path, package_name=None):
        """
        Add a directory for importer plugins and associated package name.
        @type path: str
        @param path: importer plugin directory
        @type package_name: str or None
        @param package_name: optional package name for importation
        """
        self._check_path(path)
        self.importer_paths[path] = package_name or ''

    def add_distributor_config_path(self, path):
        """
        Add a directory for distributor configuration files.
        @type path: str
        @param path: distributor configuration directory
        """
        self._check_path(path)
        self.distributor_config_paths.append(path)

    def add_distributor_plugin_path(self, path, package_name=None):
        """
        Add a directory for distributor plugins and associate package name.
        @type path: str
        @param path: distributor plugin directory
        @type package_name: str or None
        @param package_name: optional package name for importation
        """
        self._check_path(path)
        self.distributor_paths[path] = package_name or ''

    # plugin discovery

    def _load_configs(self, config_paths):
        """
        Load and parse plugin cofiguration files from the list directories.
        @type config_paths: list of strs
        @params config_paths: list of directories
        @rtype: dict
        @return: map of config name to SafeConfigParser instance
        """
        configs = {}
        files_regex = re.compile('.*\.conf$')
        for path in config_paths:
            files = os.listdir(path)
            for file_name in filter(files_regex.match, files):
                if file_name in configs:
                    raise ConflictingPluginError(_('More than one configuration file found for %s') % file_name)
                parser = SafeConfigParser()
                parser.read(os.path.join(path, file_name))
                configs[file_name] = parser
        return configs

    def _load_modules(self, plugin_paths, skip=None):
        """
        Load python modules from the list of plugin directories.
        @type plugin_paths: tuple or list of strs
        @param plugin_paths: list of directories
        @type skip: tuple or list of strs
        @param skip: optional list of module names to skip
        @rtype: list of modeule instances
        @return: all modules in the list of directories not in the skip list
        """
        skip = skip or ('__init__', 'base') # don't load package or base modules
        files_regex = re.compile('(?!(%s))\.py$') % '|'.join(skip)
        modules = []
        for path, package_name in paths.items():
            files = os.listdir(path)
            for file_name in filter(files_regex.match, files):
                name = file_name.rsplit('.', 1)[0]
                module_name = '.'.join((package_name, name))
                module = import_module(module_name)
                modules.append(module)
        return modules

    def _is_plugin_enabled(self, pulgin_name, config):
        """
        Grok through a config parser and see if the plugin is not disabled.
        @type config: SafeConfigParser instance
        @param config: plugin config
        @rtype: bool
        @return: True if the plugin is enabled, False otherwise
        """
        if config is None:
            return True
        if not config.has_section(plugin_name):
            return True
        if not config.has_option(plugin_name, 'enabled'):
            return True
        return config.getboolean(plugin_name, 'enabled')

    def load_importers(self):
        """
        Load all importer modules and associate them with their supported types.
        """
        assert not (self.importer_plugins or self.importer_configs)
        configs = self._load_configs(self.importer_config_paths)
        modules = self._load_modules(self.importer_plugin_paths)
        for module in modules:
            for attr in dir(module):
                if not issubclass(attr, Importer):
                    continue
                metadata = attr.metadata()
                name = metadata.get('name', None)
                version = metadata.get('version', None)
                types = metadata.get('types', ())
                conf_file = metadata.get('conf_file', None)
                if name is None:
                    raise MalformedPluginError(_('Importer discoverd with no name metadata: %s') %
                                               attr.__name__)
                cfg = configs.get(conf_file, None)
                if not self._is_plugin_enabled(name, cfg):
                    continue
                plugin_versions = self.importer_plugins.setdefault('name', {})
                if version in plugin_versions:
                    raise ConflictingPluginError(_('Two importers %s version %s found') %
                                                 (name, str(version)))
                plugin_versions[version] = attr
                config_versions = self.importer_configs.setdefault('name', {})
                config_versions[version] = cfg or SafeConfigParser()
                _log.info(_('Importer plugin %s version %s loaded for content types: %s') %
                          (name, str(version), ','.join(types)))

    def load_distributors(self):
        """
        Load all distributor modules and associate them with their supported types.
        """
        assert not (self.distributor_plugins or self.distributor_configs)
        configs = self._load_configs(self.distributor_config_paths)
        modules = self._load_modules(self.distributor_plugin_paths)
        for module in modules:
            for attr in dir(module):
                if not issubclass(attr, Distributor):
                    continue
                metadata = attr.metadata()
                name = metadata.get('name', None)
                version = metadata.get('version', None)
                types = metadata.get('types', ())
                conf_file = metadata.get('conf_file', None)
                if name is None:
                    raise MalformedPluginError(_(''))
                cfg = configs.get(conf_file, None)
                if not self._is_plugin_enabled(name, cfg):
                    continue
                plugin_versions = self.distributor_plugins.setdefault('name', {})
                if version in plugin_versions:
                    raise ConflictingPluginError(_(''))
                plugin_versions[version] = attr
                config_versions = self.distributor_configs.setdefault('name', {})
                config_versions[version] = cfg or SafeConfigParser()
                _log.info(_(''))

    # importer/distributor lookup api

    def _get_latest_version(self, versions):
        pass

    def get_importer_class(self, name, version=None):
        pass

    def get_importer_config(self, name, version=None):
        pass

    def get_distributor_class(self, name, version=None):
        pass

    def get_distributor_config(self, name, version):
        pass

    # query api

    def get_loaded_importers(self):
        pass

    def get_loaded_distributors(self):
        pass

# manager api ------------------------------------------------------------------

def _create_manager():
    global _manager
    _manager = Manager()


def _add_paths():
    _manager.add_importer_config_path(_importer_configs_dir)
    _manager.add_importer_plugin_path(_importer_plugins_dir,
                                      _importer_plugins_package)
    _manager.add_distributor_config_path(_distributor_configs_dir)
    _manager.add_distributor_plugin_path(_distributor_plugins_dir,
                                         _distributor_plugins_package)


def _load_plugins():
    _manager.load_importers()
    _manager.load_distributors()


def initialize():
    """
    Initialize importer/distributor plugin discovery and association.
    """
    # NOTE this is broken down into the the helper functions: _create_manager,
    # _add_paths, and _load_plugins to facilitate testing and other alternate
    # control flows on startup
    global _manager
    assert _manager is None
    _create_manager()
    _add_paths()
    _load_plugins()


def finalize():
    """
    Conduct and necessary cleanup of the plugn manager.
    """
    # NOTE this is not necessary for the pulp server but is provided for testing
    global _manager
    assert _manager is not None
    tmp = _manager
    _manager = None
    del tmp

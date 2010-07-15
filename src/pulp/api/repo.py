#!/usr/bin/python
#
# Copyright (c) 2010 Red Hat, Inc.
#
# This software is licensed to you under the GNU General Public License,
# version 2 (GPLv2). There is NO WARRANTY for this software, express or
# implied, including the implied warranties of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. You should have received a copy of GPLv2
# along with this software; if not, see
# http://www.gnu.org/licenses/old-licenses/gpl-2.0.txt.
#
# Red Hat trademarks are not licensed under GPLv2. No permission is
# granted to use or replicate Red Hat trademarks that are incorporated
# in this software or its documentation.


# Python
import logging
import gzip
import os
import traceback

# Pulp
from pulp import comps_util
from pulp import crontab
from pulp import model
from pulp import upload
from pulp.api import repo_sync
from pulp.api.base import BaseApi
from pulp.api.package import PackageApi
from pulp.auditing import audit
from pulp.pexceptions import PulpException


log = logging.getLogger(__name__)

repo_fields = model.Repo(None, None, None).keys()


class RepoApi(BaseApi):
    """
    API for create/delete/syncing of Repo objects
    """

    def __init__(self, config):
        BaseApi.__init__(self, config)
        self.packageApi = PackageApi(config)
        self.localStoragePath = config.get('paths', 'local_storage')
   
    def _get_indexes(self):
        return ["packages", "packagegroups", "packagegroupcategories"]

    def _get_unique_indexes(self):
        return ["id"]

    def _getcollection(self):
        return self.db.repos

    def _validate_schedule(self, sync_schedule):
        '''
        Verifies the sync schedule is in the correct cron syntax, throwing an exception if
        it is not.
        '''
        if sync_schedule:
            item = crontab.CronItem(sync_schedule + ' null') # CronItem expects a command
            if not item.is_valid():
                raise PulpException('Invalid sync schedule specified [%s]' % sync_schedule)
            
    def _get_existing_repo(self, id):
        """
        Protected helper function to look up a repository by id and raise a
        PulpException if it is not found.
        """
        repo = self.repository(id)
        if repo is None:
            raise PulpException("No Repo with id: %s found" % id)
        return repo
 
    @audit
    def create(self, id, name, arch, feed=None, symlinks=False, sync_schedule=None):
        """
        Create a new Repository object and return it
        """
        repo = self.repository(id)
        if repo is not None:
            raise PulpException("A Repo with id %s already exists" % id)
        self._validate_schedule(sync_schedule)

        r = model.Repo(id, name, arch, feed)
        r['sync_schedule'] = sync_schedule
        r['use_symlinks'] = symlinks
        self.insert(r)

        if sync_schedule:
            repo_sync.update_schedule(self.config, r)

        return r

    @audit
    def delete(self, **kwargs):
        # XXX remove this **kwargs magic, we only need the id
        repo = self._get_existing_repo(kwargs['id'])
        repo_sync.delete_schedule(self.config, repo)
        self.objectdb.remove(repo, safe=True)

    @audit
    def update(self, repo_data):
        repo = self._get_existing_repo(repo_data['id'])
        # make sure we're only updating the fields in the model
        for field in repo_fields:
            #default to the existing value if the field isn't in the data
            repo[field] = repo_data.get(field, repo[field])
        self._validate_schedule(repo['sync_schedule'])

        self.objectdb.save(repo, safe=True)

        if repo['sync_schedule']:
            repo_sync.update_schedule(self.config, repo)
        else:
            repo_sync.delete_schedule(self.config, repo)

        return repo

    def repositories(self, spec=None, fields=None):
        """
        Return a list of Repositories
        """
        return list(self.objectdb.find(spec=spec, fields=fields))
        
    def repository(self, id, fields=None):
        """
        Return a single Repository object
        """
        repos = self.repositories({'id': id}, fields)
        if not repos:
            return None
        return repos[0]
        
    def packages(self, id, name=None):
        """
        Return list of Package objects in this Repo
        """
        repo = self._get_existing_repo(id)
        packages = repo['packages']
        # XXX this is WRONG!!!!, we are returning a dict if name is None
        # otherwise we are returning a list!
        if name is None:
            return packages
        return [p for p in packages.values() if p['name'].find(name) >= 0]
    
    def get_package(self, id, name):
        """
        Return matching Package object in this Repo
        """
        packages = self.packages(id, name)
        if not packages:
            return None
        return packages[0]
    
    @audit
    def add_package(self, repoid, packageid):
        """
        Adds the passed in package to this repo
        """
        repo = self._get_existing_repo(repoid)
        package = self.packageApi.package(packageid)
        if package is None:
            raise PulpException("No Package with id: %s found" % packageid)
        # TODO:  We might want to restrict Packages we add to only
        #        allow 1 NEVRA per repo and require filename to be unique
        self._add_package(repo, package)
        self.update(repo)

    def _add_package(self, repo, p):
        """
        Responsible for properly associating a Package to a Repo
        """
        packages = repo['packages']
        if p['id'] in packages:
            # No need to update repo, this Package is already under this repo
            return
        packages[p['id']] = p           

    @audit
    def remove_package(self, repoid, p):
        repo = self._get_existing_repo(repoid)
        # this won't fail even if the package is not in the repo's packages
        repo['packages'].pop(p['id'], None)
        self.update(repo)

    @audit
    def create_packagegroup(self, repoid, group_id, group_name, description):
        """
        Creates a new packagegroup saved in the referenced repo
        @param repoid:
        @param group_id:
        @param group_name:
        @param description:
        @return packagegroup object
        """
        repo = self._get_existing_repo(repoid)
        if group_id in repo['packagegroups']:
            raise PulpException("Package group %s already exists in repo %s" %
                                (group_id, repoid))
        group = model.PackageGroup(group_id, group_name, description)
        repo["packagegroups"][group_id] = group
        self.update(repo)
        self._update_groups_metadata(repo["id"])
        return group

    @audit
    def delete_packagegroup(self, repoid, groupid):
        """
        Remove a packagegroup from a repo
        @param repoid:
        @param group_id:
        """
        repo = self._get_existing_repo(repoid)
        if groupid not in repo['packagegroups']:
            return
        if repo['packagegroups'][groupid]["immutable"]:
            raise PulpException("Changes to immutable groups are not supported: %s" % (groupid))
        del repo['packagegroups'][groupid]
        self.update(repo)
        self._update_groups_metadata(repo["id"])

    @audit
    def update_packagegroup(self, repoid, pg):
        """
        Save the passed in PackageGroup to this repo
        @param repoid: repo id
        @param pg: packagegroup
        """
        repo = self._get_existing_repo(repoid)
        pg_id = pg['id']
        if pg_id in repo['packagegroups']:
            if repo["packagegroups"][pg_id]["immutable"]:
                raise PulpException("Changes to immutable groups are not supported: %s" % (pg["id"]))
        repo['packagegroups'][pg_id] = pg
        self.update(repo)
        self._update_groups_metadata(repo["id"])

    @audit
    def update_packagegroups(self, repoid, pglist):
        """
        Save the list of passed in PackageGroup objects to this repo
        @param repoid: repo id
        @param pg: list of packagegroups
        """
        repo = self._get_existing_repo(repoid)
        for item in pglist:
            if item['id'] in repo['packagegroups']:
                if repo['packagegroups'][item["id"]]["immutable"]:
                    raise PulpException("Changes to immutable groups are not supported: %s" % (item["id"]))
            repo['packagegroups'][item['id']] = item
        self.update(repo)
        self._update_groups_metadata(repo["id"])

    def packagegroups(self, id):
        """
        Return list of PackageGroup objects in this Repo
        @param id: repo id
        @return: packagegroup or None
        """
        repo = self._get_existing_repo(id)
        return repo['packagegroups']
    
    def packagegroup(self, repoid, groupid):
        """
        Return a PackageGroup from this Repo
        @param repoid: repo id
        @param groupid: packagegroup id
        @return: packagegroup or None
        """
        repo = self._get_existing_repo(repoid)
        return repo['packagegroups'].get(groupid, None)

    
    @audit
    def add_package_to_group(self, repoid, groupid, pkg_name, gtype="default"):
        """
        @param repoid: repository id
        @param groupid: group id
        @param pkg_name: package name
        @param gtype: OPTIONAL type of package group,
            example "mandatory", "default", "optional"
        """
        repo = self._get_existing_repo(repoid)
        if groupid not in repo['packagegroups']:
            raise PulpException("No PackageGroup with id: %s exists in repo %s" 
                                % (groupid, repoid))
        group = repo["packagegroups"][groupid]
        if group["immutable"]:
            raise PulpException("Changes to immutable groups are not supported: %s" % (group["id"]))
        if gtype == "mandatory":
            if pkg_name not in group["mandatory_package_names"]:
                group["mandatory_package_names"].append(pkg_name)
        elif gtype == "conditional":
            raise NotImplementedError("No support for creating conditional groups")
        elif gtype == "optional":
            if pkg_name not in group["optional_package_names"]:
                group["optional_package_names"].append(pkg_name)
        else:
            if pkg_name not in group["default_package_names"]:
                group["default_package_names"].append(pkg_name)
        self.update(repo)
        self._update_groups_metadata(repo["id"])
        
        
    @audit
    def delete_package_from_group(self, repoid, groupid, pkg_name, gtype="default"):
        """
        @param repoid: repository id
        @param groupid: group id
        @param pkg_name: package name
        @param gtype: OPTIONAL type of package group,
            example "mandatory", "default", "optional"
        """
        repo = self._get_existing_repo(repoid)
        if groupid not in repo['packagegroups']:
            raise PulpException("No PackageGroup with id: %s exists in repo %s" 
                                % (groupid, repoid))
        group = repo["packagegroups"][groupid]
        if group["immutable"]:
            raise PulpException("Changes to immutable groups are not supported: %s" % (group["id"]))
        if gtype == "mandatory":
            if pkg_name in group["mandatory_package_names"]:
                group["mandatory_package_names"].remove(pkg_name)
        elif gtype == "conditional":
            raise NotImplementedError("No support for creating conditional groups")
        elif gtype == "optional":
            if pkg_name in group["optional_package_names"]:
                group["optional_package_names"].remove(pkg_name)
        else:
            if pkg_name in group["default_package_names"]:
                group["default_package_names"].remove(pkg_name)
        self.update(repo)
        self._update_groups_metadata(repo["id"])
    
    @audit
    def create_packagegroupcategory(self, repoid, cat_id, cat_name, description):
        """
        Creates a new packagegroupcategory saved in the referenced repo
        @param repoid:
        @param cat_id:
        @param cat_name:
        @param description:
        @return packagegroupcategory object
        """
        repo = self._get_existing_repo(repoid)
        if cat_id in repo['packagegroupcategories']:
            raise PulpException("Package group category %s already exists in repo %s" %
                                (cat_id, repoid))
        cat = model.PackageGroupCategory(cat_id, cat_name, description)
        repo["packagegroupcategories"][cat_id] = cat
        self.update(repo)
        self._update_groups_metadata(repo["id"])
        return cat
    
    @audit
    def delete_packagegroupcategory(self, repoid, categoryid):
        """
        Remove a packagegroupcategory from a repo
        """
        repo = self._get_existing_repo(repoid)
        if categoryid not in repo['packagegroupcategories']:
            return
        if repo['packagegroupcategories'][categoryid]["immutable"]:
            raise PulpException("Changes to immutable categories are not supported: %s" % (categoryid))
        del repo['packagegroupcategories'][categoryid]
        self.update(repo)
        self._update_groups_metadata(repo["id"])

    @audit
    def update_packagegroupcategory(self, repoid, pgc):
        """
        Save the passed in PackageGroupCategory to this repo
        """
        repo = self._get_existing_repo(repoid)
        if pgc['id'] in repo['packagegroupcategories']:
            if repo["packagegroupcategories"][pgc["id"]]["immutable"]:
                raise PulpException("Changes to immutable categories are not supported: %s" % (pgc["id"]))
        repo['packagegroupcategories'][pgc['id']] = pgc
        self.update(repo)
        self._update_groups_metadata(repo["id"])
    
    @audit
    def update_packagegroupcategories(self, repoid, pgclist):
        """
        Save the list of passed in PackageGroupCategory objects to this repo
        """
        repo = self._get_existing_repo(repoid)
        for item in pgclist:
            if item['id'] in repo['packagegroupcategories']:
                if repo["packagegroupcategories"][item["id"]]["immutable"]:
                    raise PulpException("Changes to immutable categories are not supported: %s" % item["id"])
            repo['packagegroupcategories'][item['id']] = item
        self.update(repo)
        self._update_groups_metadata(repo["id"])

    def packagegroupcategories(self, id):
        """
        Return list of PackageGroupCategory objects in this Repo
        """
        repo = self._get_existing_repo(id)
        return repo['packagegroupcategories']

    def packagegroupcategory(self, repoid, categoryid):
        """
        Return a PackageGroupCategory object from this Repo
        """
        repo = self._get_existing_repo(repoid)
        return repo['packagegroupcategories'].get(categoryid, None)

    def _update_groups_metadata(self, repoid):
        """
        Updates the groups metadata (example: comps.xml) for a given repo
        @param repoid: repo id
        @return: True if metadata was successfully updated, otherwise False
        """
        repo = self._get_existing_repo(repoid)
        try:
            # If the repomd file is not valid, or if we are missingg
            # a group metadata file, no point in continuing. 
            if not os.path.exists(repo["repomd_xml_path"]):
                log.debug("Skipping update of groups metadata since missing repomd file: '%s'" % 
                          (repo["repomd_xml_path"]))
                return False
            xml = comps_util.form_comps_xml(repo['packagegroupcategories'],
                repo['packagegroups'])
            if repo["group_xml_path"] == "":
                repo["group_xml_path"] = os.path.dirname(repo["repomd_xml_path"])
                repo["group_xml_path"] = os.path.join(os.path.dirname(repo["repomd_xml_path"]),
                                                      "comps.xml")
                self.update(repo)
            f = open(repo["group_xml_path"], "w")
            f.write(xml.encode("utf-8"))
            f.close()
            if repo["group_gz_xml_path"]:
                gz = gzip.open(repo["group_gz_xml_path"], "wb")
                gz.write(xml.encode("utf-8"))
                gz.close()
            return comps_util.update_repomd_xml_file(repo["repomd_xml_path"],
                repo["group_xml_path"], repo["group_gz_xml_path"])
        except Exception, e:
            log.debug("_update_groups_metadata exception caught: %s" % (e))
            log.debug("Traceback: %s" % (traceback.format_exc()))
            return False
       
    @audit
    def sync(self, id):
        """
        Sync a repo from the URL contained in the feed
        """
        repo = self._get_existing_repo(id)
        repo_source = repo['source']
        if not repo_source:
            raise PulpException("This repo is not setup for sync. Please add packages using upload.")
        added_packages = repo_sync.sync(self.config, repo, repo_source)
        for p in added_packages:
            self._add_package(repo, p)
        self.update(repo)

    @audit
    def upload(self, id, pkginfo, pkgstream):
        """
        Store the uploaded package and associate to this repo
        """
        repo = self._get_existing_repo(id)
        pkg_upload = upload.PackageUpload(self.config, repo, pkginfo, pkgstream)
        pkg, repo = pkg_upload.upload()
        self._add_package(repo, pkg)
        self.update(repo)
        log.info("Upload success %s %s" % (pkg['id'], repo['id']))
        return True

    def all_schedules(self):
        '''
        For all repositories, returns a mapping of repository name to sync schedule.
        
        @rtype:  dict
        @return: key - repo name, value - sync schedule
        '''
        return dict((r['id'], r['sync_schedule']) for r in self.repositories())
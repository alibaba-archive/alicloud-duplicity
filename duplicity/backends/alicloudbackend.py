# -*- Mode:Python; indent-tabs-mode:nil; tab-width:4 -*-
#
# Copyright 2017 hongxu <luluhongxu@gmail.com>
#
# This file is part of duplicity.
#
# Duplicity is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the
# Free Software Foundation; either version 2 of the License, or (at your
# option) any later version.
#
# Duplicity is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with duplicity; if not, write to the Free Software Foundation,
# Inc., 59 Temple Place, Suite 330, Boston, MA 02111-1307 USA

import os
import duplicity.backend
from duplicity import globals
from duplicity import log
from duplicity.errors import FatalBackendException, BackendException
from duplicity import progress

class AliCloudBackend(duplicity.backend.Backend):
    """
    Backend for AliCloud OSS Storage Serive.

    To make use of this backend you must set endpoint and access_key_id
    and access_key_secret in the oss section in your ~/.alicloud.cfg.
    Alternatively you can export the environment variables
    ALICLOUD_OSS_ENDPOINT and ALICLOUD_ACCESS_KEY_ID and ALICLOUD_ACCESS_KEY_SECRET.
    """
    
    def __init__(self, parsed_url):
        duplicity.backend.Backend.__init__(self, parsed_url)

        # Import AliCloud OSS SDK for Python library. 
        try:
            import oss2
        except ImportError:
            raise BackendException("AliCloud backend requires OSS Python SDK library.")

        self.get_alicloud_configuration()

        self.scheme = parsed_url.scheme
        if parsed_url.hostname:
            self.bucket_name = parsed_url.hostname 
        else:
            raise BackendException("AliCloud OSS requires a bucket name.")

        # Make //bucket_name///key_prefix/ and //bucket_name/key_prefix equivalent.
        self.key_prefix = ''
        self.url_parts = [x for x in parsed_url.path.split('/') if x != ''] 
        if self.url_parts:
            self.key_prefix = '%s/' % '/'.join(self.url_parts)

        self.straight_url = duplicity.backend.strip_auth_from_url(parsed_url)
        self.reset_connection()

    def _put(self, source_path, remote_filename):
        key_name = self.key_prefix + remote_filename
        
        # Default multipart parallel threads number is 5.
        multipart_parallel = 5
        if vars().has_key('globals.alicloud_multipart_upload_threads'):
            if globals.alicloud_multipart_upload_threads >= 5:
                multipart_parallel = globals.alicloud_multipart_upload_threads
                
        import oss2
        oss2.resumable_upload(self.bucket, key_name, source_path.name, num_threads=multipart_parallel)

    def _get(self, remote_filename, local_path):
        key_name = self.key_prefix + remote_filename
        self.bucket.get_object_to_file(key_name, local_path.name)

    def _list(self):
        return self.list_filenames_in_bucket()

    def _delete(self, filename):
        self.bucket.delete_object(self.key_prefix + filename)

    def _delete_list(self, filename_list):
        for filename in filename_list:
            self._delete(filename)

    def _query(self, filename):
        """Get the file size"""
        import oss2
        key_name = self.key_prefix + filename
        try:
            file_size = self.bucket.get_object_meta(key_name).content_length
        except oss2.exceptions.NoSuchKey :
            file_size = -1
        return {'size': file_size}

    def _query_list(self, filename_list):
        """
        Query metadata of a list of files. 
        Return a dict of filenames mapping to a dict with a 'size' key, 
        and a file size value (-1 for not found)
        """
        query_dict = {}
        for filename in filename_list:
            query_dict[filename] = self._query(filename)
        return query_dict

    def _retry_cleanup(self):
        self.reset_connection()

    def _close(self):
        self.bucket = None
        del self.bucket

    def list_filenames_in_bucket(self):
        import oss2
        filename_list = []
        for item in oss2.ObjectIterator(self.bucket, prefix=self.key_prefix):
            try:
                filename = item.key.replace(self.key_prefix, '', 1)
                filename_list.append(filename)
                log.Debug("Listed %s/%s" % (self.straight_url, filename))
            except AttributeError:
                log.Error("List AttributeError")

        return filename_list

    def reset_connection(self):
        self.bucket = None
        del self.bucket

        import oss2
        self.auth = oss2.Auth(self.access_key_id, self.access_key_secret)
        self.bucket = oss2.Bucket(self.auth, self.endpoint, self.bucket_name)
        
        if not self.bucket_exists():
            self.bucket.create_bucket()

    def bucket_exists(self):
        import oss2
        try:
            self.bucket.get_bucket_acl()
            return True
        except oss2.exceptions.NoSuchBucket:
            return False 

    def get_alicloud_configuration(self):
        """
        Get AliClould oss configuration firstly from enviroment variables. 
        If the enviroment variables not set, then read from ~/.alicloud.cfg. 
        """
        config_filename = os.path.expanduser('~') + '/.alicloud.cfg'
        self.endpoint, self.access_key_id, self.access_key_secret = '', '', '' 
        
        # Get configuration from enviroment variable
        if 'ALICLOUD_OSS_ENDPOINT' in os.environ and 'ALICLOUD_ACCESS_KEY_ID' in os.environ and 'ALICLOUD_ACCESS_KEY_SECRET' in os.environ:
            self.endpoint = os.environ['ALICLOUD_OSS_ENDPOINT']
            self.access_key_id = os.environ['ALICLOUD_ACCESS_KEY_ID']
            self.access_key_secret = os.environ['ALICLOUD_ACCESS_KEY_SECRET']
            if self.endpoint == '' or self.access_key_id == '' or self.access_key_secret == '':
                raise BackendException("AliCloud OSS Endpoint or AccessKeyId or AccessKeySecret got from environment variables are invalid.")
            return

        log.Debug("You have not set environment variables, use the configuration file %s instead." % config_filename)
        if not os.path.exists(config_filename):
            raise BackendException("Please set Endpoint, AccessKeyId, AccessKeySecret in the enviroment variables or in the configuration file %s." % config_filename)
        
        try:
            import configparser
        except ImportError:
            raise BackendException("AliCloud backend requires configparser module.")
        
        # Load configuration from '~/.alicloud.cfg'.
        cfgparser = configparser.SafeConfigParser()
        cfgparser.read(config_filename)
        self.endpoint = cfgparser.get("oss", "endpoint")
        self.access_key_id = cfgparser.get("oss", "access_key_id")
        self.access_key_secret = cfgparser.get("oss", "access_key_secret")

        if self.endpoint == '' or self.access_key_id == '' or self.access_key_secret == '':
            raise BackendException("AliCloud OSS Endpoint or AccessKeyId or AccessKeySecret in %s are invalid." % config_filename)
        return

duplicity.backend.register_backend("oss", AliCloudBackend)
duplicity.backend.uses_netloc.extend(['oss'])

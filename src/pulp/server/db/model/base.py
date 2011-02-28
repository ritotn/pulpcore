# -*- coding: utf-8 -*-

# Copyright © 2010 Red Hat, Inc.
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

import uuid
from gettext import gettext as _

from pymongo import DESCENDING
from pymongo.collection import Collection

from pulp.server.db.connection import get_database


class Model(dict):
    """
    Model base class

    Derived model classes are the representation of persistent data used by
    pulp and are abstractions of the documents used by mongodb. These classes 
    are used to create new documents to be stored in a document collection.

    The model base class is derived from the builtin dictionary, and should be 
    used as such by code after instantiation. It provides a mechanism to use 
    dot notation instead of the usual dictionary key lookup. However, this is 
    provided for convenience when declaring fields in the constructors, and 
    should not be used by code. Documents that are retrieved from a document  
    collection are also derivatives of dictionaries, but are not derivatives 
    of the Model class. To ensure interchangability, make sure to use python's
    regular dictionary key lookup when using Model instances.
    """

    # The model class will know how to fetch the document collection used to
    # store the models in database. If you want a document collection to be
    # associated with your model, all you need to do is define the name of the
    # document collection with the 'collection_name' class field.
    # Once you have defined the collection_name, you may use the
    # 'unique_indices' and 'other_indicies' to define which fields are indexed
    # in the document collection.
    # The unique_indicies field is a tuple whose elements can be either:
    # * A string name of a model field whose value is to be indexed and must be
    #   unique among all stored instances of the model.
    # * A tuple of string names of model fields that will each be indexed and,
    #   together, must be a unique set of fields among all stored instances of
    #   the model.
    # The other_indices field is only a tuple listing other model fields to be
    # indexed in the collection, but that do not need to be individually unique
    # or form unique sets of values.

    collection_name = None
    unique_indicies = ('id',) # note, '_id' is automatically unique and indexed
    other_indicies = ()

    # -------------------------------------------------------------------------

    def __init__(self):
        self._id = str(uuid.uuid4())
        self.id = self._id

    # dict to dot-notation mapping methods
    # XXX only for use in constructors

    def __getattr__(self, attr):
        return self.get(attr, None)
    __setattr__ = dict.__setitem__
    __delattr__ = dict.__delitem__

    # database collection methods ---------------------------------------------

    @classmethod
    def _get_collection_from_db(cls):
        # ensure the indicies in the document collection
        def _ensure_indicies(collection, indicies, unique):
            # indicies are either tuples or strings,
            # tuples are 'unique together' if unique is True
            for index in indicies:
                if isinstance(index, basestring):
                    index = (index,)
                # we're using descending ordering for the arbitrary case,
                # if you need a particular ordering, override the 
                # _get_collection_from_db method
                collection.ensure_index([(i, DESCENDING) for i in index],
                                        unique=unique, background=True)
        # create the collection and ensure the unique and other indicies
        db = get_database()
        if db is None:
            msg = _('Cannot get collection from uninitialized database')
            raise RuntimeError(msg)
        collection = Collection(db, cls.collection_name)
        _ensure_indicies(collection, cls.unique_indicies, True)
        _ensure_indicies(collection, cls.other_indicies, False)
        return collection

    @classmethod
    def _get_cached_collection(cls):
        try:
            return cls.__collection
        except AttributeError:
            return None

    @classmethod
    def get_collection(cls):
        """
        Get the document collection for this data model.
        @rtype: pymongo.collection.Collection instance or None
        @return: the document collection if associated with one, None otherwise
        """
        # not all data models are associated with a document collection
        # provide mechanism for sub-documents by not defining the
        # collection_name
        if cls.collection_name is None:
            return None
        # see if we have the collection cached
        cls.__collection = cls._get_cached_collection()
        # if not, grab the collection, and cache it
        if cls.__collection is None:
            cls.__collection = cls._get_collection_from_db()
        return cls.__collection

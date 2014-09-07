##############################################################################
# Copyright by The HDF Group.                                                #
# All rights reserved.                                                       #
#                                                                            #
# This file is part of H5Serv (HDF5 REST Server) Service, Libraries and      #
# Utilities.  The full HDF5 REST Server copyright notice, including          #
# terms governing use, modification, and redistribution, is contained in     #
# the file COPYING, which can be found at the root of the source code        #
# distribution tree.  If you do not have access to this file, you may        #
# request a copy from help@hdfgroup.org.                                     #
##############################################################################

"""
This class is used to manage UUID lookup tables for primary HDF objects (Groups, Datasets,
 and Datatypes).  For HDF5 files that are read/write, this information is managed within 
 the file itself in the "__db__" group.  For read-only files, the data is managed in 
 an external file.
 
 "___db__"  ("root" for read-only case) 
    description: Group object (member of root group). Only objects below this group are used 
            for UUID data
    members: "{groups}", "{datasets}", "{datatypes}", "{objects}", "{paths}"
    attrs: 'rootUUID': UUID of the root group
    
"{groups}"  
    description: contains map of UUID->group objects  (read/write only)
    members: group reference to each group (other than root and groups in __db__)
        in the file.  Link name is the UUID
    attrs: none
    
"{datasets}"  
    description: contains map of UUID->dataset objects  (read/write only)
    members: dataset reference to each dataset  in the file.  Link name is the UUID.
    attrs: none
    
"{datatypes}"  
    description: contains map of UUID->datatype objects  (read/write only)
    members: dataset reference to each datatype in the file.  Link name is the UUID.
    attrs: none
    
"{paths}"
    description: contains map of UUID->paths (read-only)
    members: none
    attrs: map of UUID to path

"{addr}"
    description: contains map of file offset to UUID
    members: none
    attrs: map of file offset to UUID
        
    
    
 
"""

import h5py
import shutil
import uuid
import logging
import os.path as op
import os


# global dictionary to direct back to the Hdf5db instance by filename
# (needed for visititems callback)
# Will break in multi-threaded context
_db = { }

def visitObj(path, obj):   
    hdf5db = _db[obj.file.filename]
    hdf5db.visit(path, obj)
    
class Hdf5db:
    @staticmethod
    def isHDF5File(filePath):
        return h5py.is_hdf5(filePath)
        
    @staticmethod
    def createHDF5File(filePath):
        # create an "empty" hdf5 file
        if op.isfile(filePath):
            # already a file there!
            return False
        f = h5py.File(filePath, 'w')
        f.close()
        return True
           
        
    def __init__(self, filePath):
        if os.access(filePath, os.W_OK):         
            mode = 'r+'
            self.readonly = False
        else:
            mode = 'r'
            self.readonly = True
        #logging.info("init -- filePath: " + filePath + " mode: " + mode)
        
        self.f = h5py.File(filePath, mode)
        
        if self.readonly:
            dbFilePath = self.f.filename + ".db"
            dbMode = 'r+'
            if not op.isfile(dbFilePath):
                dbMode = 'w'
            logging.info("dbFilePath: " + dbFilePath + " mode: " + dbMode)
            self.dbf = h5py.File(dbFilePath, dbMode)
        else:
            self.dbf = None # for read only
        # create a global reference to this class
        # so visitObj can call back
        _db[filePath] = self 
    
    def __enter__(self):
        logging.info('Hdf5db __enter')
        return self

    def __exit__(self, type, value, traceback):
        logging.info('Hdf5db __exit')
        filename = self.f.filename
        self.f.flush()
        self.f.close()
        if self.dbf:
            self.dbf.flush()
            self.dbf.close()
        del _db[filename]
        
    def initFile(self):
        # logging.info("initFile")
        self.httpStatus = 200
        self.httpMessage = None
        if self.readonly:
            self.dbGrp = self.dbf
            if "{groups}" in self.dbf:
                # file already initialized
                return
            
        else:
            if "__db__" in self.f:
                # file already initialized
                self.dbGrp = self.f["__db__"]
                return;  # already initialized 
            self.dbGrp = self.f.create_group("__db__")
           
        logging.info("initializing file") 
        self.dbGrp.attrs["rootUUID"] = str(uuid.uuid1())
        grps = self.dbGrp.create_group("{groups}")
        self.dbGrp.create_group("{datasets}")
        self.dbGrp.create_group("{datatypes}")
        self.dbGrp.create_group("{addr}") # store object address
        if self.readonly:
            self.dbGrp.create_group("{paths}")  # store paths
            
        self.f.visititems(visitObj)
        
    def visit(self, path, obj):
        name = obj.__class__.__name__
        if len(path) >= 6 and path[:6] == '__db__':
            return  # don't include the db objects
        logging.info('visit: ' + path +' name: ' + name)
        col = None 
        if name == 'Group':
            col = self.dbGrp["{groups}"]
        elif name == 'Dataset':
            col = self.dbGrp["{datasets}"]
        elif name == 'Datatype':
            col = self.dbGrp["{datatypes}"]
        else:
            logging.error("unknown type: " + __name__)
            self.httpStatus = 500
            self.httpMessage = "Unexpected error"
            return
        uuid1 = uuid.uuid1()  # create uuid
        id = str(uuid1)
        addrGrp = self.dbGrp["{addr}"]
        if not self.readonly:
            # storing db in the file itself, so we can link to the object directly
            col[id] = obj  # create new link to object
        else:
            #store id->path in paths group
            if "{paths}" not in self.dbGrp:
                logging.error("expected to find {paths} group")
                raise Exception
            paths = self.dbGrp["{paths}"]
            paths.attrs[id] = obj.name
        addr = h5py.h5o.get_info(obj.id).addr
        # store reverse map as an attribute
        addrGrp.attrs[str(addr)] = id       
        
    def getUUIDByAddress(self, addr):
        if "{addr}" not in self.dbGrp:
            logging.error("expected to find {addr} group") 
            raise Exception
        addrGrp = self.dbGrp["{addr}"]
        objUuid = None
        if str(addr) in addrGrp.attrs:
            objUuid = addrGrp.attrs[str(addr)] 
        return objUuid
        
    def getUUIDByPath(self, path):
        self.initFile()
        if len(path) >= 6 and path[:6] == '__db__':
            logging.error("getUUIDByPath called with invalid path: [" + path + "]")
            raise Exception
        if path == '/':
            # just return the root UUID
            return self.dbGrp.attrs["rootUUID"]
            
        obj = self.f[path]  # will throw KeyError if object doesn't exist
        addr = h5py.h5o.get_info(obj.id).addr
        objUuid = self.getUUIDByAddress(addr)
        return objUuid
                     
    def getObjByPath(self, path):
        if len(path) >= 6 and path[:6] == '__db__':
            return None # don't include the db objects
        obj = self.f[path]  # will throw KeyError if object doesn't exist
        return obj
        
    def getDatasetByUuid(self, objUuid):
        self.initFile()
        obj = None
        if not self.readonly:
            # dataset references stored in file
            datasets = self.dbGrp["{datasets}"]
            if objUuid in datasets:
                obj = datasets[objUuid]
        else: 
            if "{paths}" not in self.dbGrp:
                raise Exception
            paths = self.dbGrp["{paths}"]
            if objUuid in paths.attrs:
                path = paths.attrs[objUuid] 
                obj = self.f[path]
                # verify this is a dataset
                if obj.__class__.__name__ != "Dataset":
                    obj = None
                                
        if obj == None:
            self.httpStatus = 404  # Not Found
            self.httpMessage = "Resource not found"
        return obj
        
    def getGroupByUuid(self, objUuid):
        logging.info("getGroupByUuid(" + objUuid + ")")
        self.initFile()
        obj = None
        if objUuid == self.dbGrp.attrs["rootUUID"]:
            obj = self.f['/']  # returns group instance
        elif not self.readonly:
            # group references stored in file
            groups = self.dbGrp["{groups}"]
            if objUuid in groups:
                obj = groups[objUuid]
        else: 
            if "{paths}" not in self.dbGrp:
                raise Exception
            paths = self.dbGrp["{paths}"]
            if objUuid in paths.attrs:
                path = paths.attrs[objUuid] 
                obj = self.f[path]
                # verify this is a group
                if obj.__class__.__name__ != "Group":
                    obj = None
     
        if obj == None:
            self.httpStatus = 404  # Not Found
            self.httpMessage = "Resource not found"
        return obj
        
    def getItems(self, grpUuid, classFilter=None, marker=None, limit=0):
        logging.info("db.getItems(" + grpUuid + ")")
        if classFilter:
            logging.info("...classFilter: " + classFilter)
        if marker:
            logging.info("...marker: " + marker)
        if limit:
            logging.info("...limit: " + str(limit))
        
        self.initFile()
        parent = self.getGroupByUuid(grpUuid)
        if parent == None:
            return None
        items = []
        gotMarker = True
        if marker != None:
            gotMarker = False
        count = 0
        for k in parent:
            if k == "__db__":
                continue
            if not gotMarker:
                if k == marker:
                    gotMarker = True
                    continue  # start filling in result on next pass
                else:
                    continue  # keep going!
            item = { 'name': k } 
            # get the link object, one of HardLink, SoftLink, or ExternalLink
            linkObj = parent.get(k, None, False, True)
            linkClass = linkObj.__class__.__name__
            if linkClass == 'SoftLink':
                if classFilter and classFilter != 'SoftLink':
                    continue
                item['class'] = 'SoftLink'
                item['path'] = linkObj.path
            elif linkClass == 'ExternalLink':
                if typeFilter and typeFilter != 'ExternalLink':
                    continue
                item['class'] = 'ExternalLink'
                item['path'] = linkObj.path
                item['filename'] = linkObj.path
            elif linkClass == 'HardLink':
                # Hardlink doesn't have any properties itself, just get the linked
                # object
                obj = parent[k]
                objClass = obj.__class__.__name__
                if classFilter and objClass != classFilter:
                    continue  # not what we are looking for
                addr = h5py.h5o.get_info(obj.id).addr
                item['class'] = objClass
                item['uuid'] = self.getUUIDByAddress(addr)
                item['attributeCount'] = len(obj.attrs)
            else:
                logging.error("unexpected classname: " + objClass)
                continue
                           
            items.append(item)
            count += 1
            print "Limit:", limit, "limittype:", type(limit), "count:", count
            if limit > 0 and count == limit:
                break  # return what we got
        return items
        
    def linkObject(self, parentUUID, childUUID, linkName):
        self.initFile()
        if self.readonly:
            self.httpStatus = 403  # Forbidden
            self.httpMessage = "Updates are not allowed"
            return False    
        parentObj = self.getGroupByUuid(parentUUID)
        if parentObj == None:
            self.httpStatus = 404 # Not found
            self.httpMessage = "Parent Group not found"
            return False
        childObj = self.getDatasetByUuid(childUUID)
        if childObj == None:
            # maybe it's a group...
            childObj = self.getGroupByUuid(childUUID)
        if childObj == None:
            # todo - can a group link to anything else?
            self.httpStatus = 404 # Not found
            self.httpMessage = "Child object not found"
            return False
        if linkName in parentObj:
            # link already exists
            logging.info("linkname already exists, deleting")
            del parentObj[linkName]  # delete old link
        parentObj[linkName] = childObj
        return True
        
    def createSoftLink(self, parentUUID, linkPath, linkName):
        self.initFile()
        if self.readonly:
            self.httpStatus = 403  # Forbidden
            self.httpMessage = "Updates are not allowed"
            return False    
        parentObj = self.getGroupByUuid(parentUUID)
        if parentObj == None:
            self.httpStatus = 404 # Not found
            self.httpMessage = "Parent Group not found"
            return False
        if linkName in parentObj:
            # link already exists
            logging.info("linkname already exists, deleting")
            del parentObj[linkName]  # delete old link
        parentObj[linkName] = h5py.SoftLink(linkPath)
        return True
        
    def unlinkItem(self, grpUuid, linkName):
        grp = self.getGroupByUuid(grpUuid)
        if grp == None:
            logging.info("parent group not found")
            self.httpStatus = 404 # not found
            return False 
            
        if linkName not in grp:
            logging.info("linkName not found")
            return True
        
        del grp[linkName]
        return True
        
    def unlinkObject(self, parentGrp, tgtObj):
        for name in parentGrp:
            linkObj = parentGrp.get(name, None, False, True)
            linkClass = linkObj.__class__.__name__
            # only deal with HardLinks
            if linkClass == 'HardLink':
                obj = parentGrp[name]
                if obj == tgtObj:
                    logging.info("deleting link: [" + name + "] from: " + parentGrp.name)
                    del parentGrp[name]                 
        return True
        
    def createGroup(self):
        self.initFile()
        if self.readonly:
            self.httpStatus = 403  # Forbidden
            self.httpMessage = "Updates are not allowed"
            return None   
        groups = self.dbGrp["{groups}"]
        objUuid = str(uuid.uuid1())
        newGroup = groups.create_group(objUuid)
        # store reverse map as an attribute
        addr = h5py.h5o.get_info(newGroup.id).addr
        addrGrp = self.dbGrp["{addr}"]
        addrGrp.attrs[str(addr)] = objUuid
        return objUuid
        
    def deleteGroup(self, objUuid):
        self.initFile()
        if self.readonly:
            self.httpStatus = 403  # Forbidden
            self.httpMessage = "Updates are not allowed"
            return False   
        if objUuid == self.dbGrp.attrs["rootUUID"]:
            self.httpStatus = 403  # Forbidden
            self.httpMessage = "Can't delete root group"
            return False  
        tgtGrp = self.getGroupByUuid(objUuid)
        if not tgtGrp:
            return False  # httpStatus should be set by getGroupByUUID
        self.unlinkObject(self.f['/'], tgtGrp)  # unlink from root
        groups = self.dbGrp["{groups}"]
        # iterate through each group in the file
        for uuidName in groups:
            grp = groups[uuidName]
            self.unlinkObject(grp, tgtGrp) 
                  
        addr = h5py.h5o.get_info(tgtGrp.id).addr
        addrGrp = self.dbGrp["{addr}"] 
        del addrGrp.attrs[str(addr)]  # remove reverse map
        del groups[objUuid]
        
    def getNumberOfGroups(self):
        self.initFile()
        groups = self.dbGrp["{groups}"]
        return len(groups) + 1  # add one for root group
           
        
    def getNumberOfDatasets(self):
        self.initFile()
        datasets = self.dbGrp["{datasets}"]
        return len(datasets)
        
    def getNumberOfDatatypes(self):
        self.initFile()
        datatypes = self.dbGrp["{datatypes}"]
        return len(datatypes)

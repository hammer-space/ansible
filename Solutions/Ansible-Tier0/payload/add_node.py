#! /usr/bin/env python3

import sys
import urllib3
import requests
from requests.auth import HTTPBasicAuth
import argparse
import uuid
import time
from pathlib import Path
import pdb

urllib3.disable_warnings()

class API:
    def __init__(self, host='data-cluster', user='admin', password='admin', port=8443):
        self._user = user
        self._password = password
        self._host = host
        self._port = port
        self._basic = HTTPBasicAuth(self._user, self._password)
        if ':' in host:
            self._host = f'[{host}]'
        else:
            self._host = host
        self._port = port
        self._mgmt_path = '/mgmt/v1.2/rest'


    def get(self, endpoint='nodes', paginate=True, identifier=None):
        returnvalue = []
        page = 0
        baseurl = f'https://{self._host}:{self._port}{self._mgmt_path}/{endpoint}'
        if identifier:
            baseurl += f'/{identifier}'
            paginate = False
        while True:
            url = baseurl
            if paginate:
                url += f'?page.size=500&page={page}'

            response = requests.get(url, auth=self._basic, verify=False)
            if not response.ok:
                print(response)
                return 
            jsondata = response.json()
            if paginate:
                print(page, len(jsondata))
            if len(jsondata) == 0:
                break
            if identifier:
                returnvalue.append(jsondata)
            else:
                returnvalue.extend(jsondata)
            if not paginate:
                break
            page += 1
        return returnvalue

    def post(self, data, endpoint='nodes'):
        url = f'https://{self._host}:{self._port}{self._mgmt_path}/{endpoint}'
        response = requests.post(url, json=data, auth=self._basic, verify=False)
        if not response.ok:
            print(response.text)
            return response, None
        
        if response.status_code == 202:
            taskurl = response.headers['Location']
            while True:
                time.sleep(.05)
                taskresponse = requests.get(taskurl, auth=self._basic, verify=False)
                taskdata = taskresponse.json()
                if taskdata.get('status', '') != 'EXCUTING':
                    break

        return response, taskresponse


    def put(self, identifier, data, endpoint='nodes'):
        url = f'https://{self._host}:{self._port}{self._mgmt_path}/{endpoint}/{identifier}'
        response = requests.put(url, json=data, auth=self._basic, verify=False)
        if not response.ok:
            print(response.text)
            return response, None
        
        if response.status_code == 202:
            taskurl = response.headers['Location']
            while True:
                time.sleep(.05)
                taskresponse = requests.get(taskurl, auth=self._basic, verify=False)
                taskdata = taskresponse.json()
                if taskdata.get('status', '') != 'EXCUTING':
                    break

        return response, taskresponse


def getuuid(path, value=None):
    uuidtarget = Path(path)
    retval = value
    if not retval and uuidtarget.exists():
        retval = uuidtarget.open().read().strip()
    if not retval:
        retval = str(uuid.uuid1())
    if not uuidtarget.exists():
        uuidtarget.open('w').write(f'{retval}\n')
        print(f'Wrote uuid to {uuidtarget.absolute()}')
    return retval

def main(args):
    api = API(args.host, args.user, args.password)
    nodes = api.get(identifier = args.node_uuid)

    dm_info = None
    cm_info = None
    if args.data_mover:
        dm_info = dict(_type='DATA_MOVER', uoid=dict(uuid=getuuid('/opt/pd/di'), objectType='DATA_MOVER'), adminState='UP', operState='UP', port=9095)
    if args.cloud_mover:
        cm_info = dict(_type='CLOUD_MOVER', uoid=dict(uuid=getuuid('/opt/pd/cm'), objectType='CLOUD_MOVER'), adminState='UP', operState='UP', port=9098)

    if not nodes:
        # Different paths for new node vs current node, so handle them separately
        postdata = dict(_type='NODE', nodeType='MOVER_EXT', uoid=dict(objectType='NODE', uuid=args.node_uuid), name=args.name, mgmtIpAddress=dict(address=args.ip, prefixLength=args.prefix_length), systemServices=[])
        if dm_info:
            postdata['systemServices'].append(dm_info)
        if cm_info:
            postdata['systemServices'].append(cm_info)

        postresponse, itemresponse = api.post(postdata)
        print(postresponse.status_code, getattr(itemresponse, 'status_code', ''))
    else:
        nodedata = nodes[0]
        changed = []
        if dm_info:
            matches = [x for x in nodedata['systemServices'] if x.get('uoid', {}).get('uuid', '') == dm_info['uoid']['uuid']]
            if matches:
                print('Data mover already defined for node')
            else:
                nodedata['systemServices'].append(dm_info)
                changed.append('Data mover added')
    
        if cm_info:
            matches = [x for x in nodedata['systemServices'] if x.get('uoid', {}).get('uuid', '') == cm_info['uoid']['uuid']]
            if matches:
                print('Cloud mover already defined for node')
            else:
                nodedata['systemServices'].append(cm_info)
                changed.append('Cloud mover added')
        if changed:
            putresponse, itemresponse = api.put(args.node_uuid, nodedata)
            print(putresponse.status_code, getattr(itemresponse, 'status_code', ''))
            print('\n'.join(changed))
        else:
            print('No services changed')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Add a node to a cluster')
    parser.add_argument('-i', '--ip')
    parser.add_argument('-l', '--prefix-length')
    parser.add_argument('-d', '--data-mover', default=False, action='store_true')
    parser.add_argument('-c', '--cloud-mover', default=False, action='store_true')
    parser.add_argument('-U', '--node-uuid')
    parser.add_argument('-u', '--user', default='admin')
    parser.add_argument('-p', '--password', default='admin')
    parser.add_argument('-P', '--port', default='8443')
    parser.add_argument('-H', '--host', default='data-cluster')
    parser.add_argument('-n', '--name')
    args = parser.parse_args()

    # Do some argument sanity checking
    msg = []
    if not args.name:
        msg.append('--name is required')
    if not args.ip:
        msg.append('--ip is required')
    if not args.prefix_length:
        msg.append('--prefix_length is required')
    if not args.data_mover and not args.cloud_mover:
        msg.append('At least one of --cloud-mover or --data-mover must be defined')
    if args.node_uuid:
        try:
            uuid.UUID(args.node_uuid)
        except ValueError:
            msg.append(f'Invalid UUID: {args.node_uuid}')
    print(args)
    if msg:
        print('\n'.join(sorted(msg)))
        sys.exit(1)

    # Validate/generate node uuid
    args.node_uuid=getuuid('/opt/pd/node_uuid', args.node_uuid)

    main(args)

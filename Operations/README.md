## Common API operations for managing Hammerspace

A set of playbooks to automate operations for Hammerspace.

Each example includes at least one variable file, [anvil.yml](anvil.yml) which contains required
information to successfully run the playbook. Some playbooks also refer to additional variable files. All of these files are located in the Configs sub-directory.

```
data_cluster_mgmt_ip: <ANVIL MGMT IP>   # management IP for Anvil
hsuser:               admin             # user with admin level role
password:             ''                # admin user password
```
### Playbooks ###
Each playbook contains a variable section with the relevant variables to the API operation.

- Active Directory ( [join](ad-join.yml) / [leave](ad-leave.yml) ) - Join and leave Active Directory
- Anti-virus ( [add](av-add.yml) / [remove](av-remove.yml) ) - Add and remove ICAP servers used for anti-virus scanning
- Identity Provider ( [add](idp-add.yml) / [remove](idp-remove.yml)) - Add and remove Identity provider for authentication of management users
- Installation [check](install-ready.yml) - Checks if an installation is ready to receive API calls.
- License ( [add](license-node-add.yml) / [remove](license-node-remove.yml) ) - Add and remove product license
- Local site ( [update](local-site-update.yml)) - Update the site name
- Objective ( [add](objective-add.yml) / [set](objective-set.yml) ) - Add and set objectives
- Object/Cloud Storage system ( [add](object-storage-system-add.yml) / [remove](object-storage-system-remove.yml) ) - Add and remove Object/Cloud storage systems (nodes)
- Object/Cloud Volume ( [add](object-storage-volume-add.yml) / [decommission](object-storage-volume-decommission.yml) /  [delete](object-storage-volume-delete.yml)) - Add and delete object storage volumes
- Proxy ( [set](proxy-set.yml) / [clear](proxy-clear.yml)) - Set and clear system proxy
- Retention ( [add](retention-add.yml) / [remove](retention-delete.yml) ) - Add and remove retention schedules
- Schedule ( [add](schedule-add.yml) / [remove](schedule-remove.yml) ) - Add and remove schedules
- Share ( [create](share-create.yml) / [delete](share-delete.yml)) - Create and delete shares
- Share snapshot ( [add](share-snapshot-add.yml) / [remove](share-snapshot-remove.yml) ) - Configure share snapshot schedules
- Storage system ( [add](storage-system-add.yml) / [remove](storage-system-remove.yml) ) - Add and remove storage systems (nodes)
- Syslog ( [add](syslog-add.yml) / [remove](syslog-remove.yml) ) - Add and remove syslog servers
- System backup ( [add](system-backup-add.yml) / [remove](system-backup-remove.yml) ) - Add and remove system and metadata backups
- Volume add ( [add](storage-volume-add.yml) ) - Add storage volume

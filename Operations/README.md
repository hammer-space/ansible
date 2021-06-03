## Common API operations for managing a Hammerspace installation

A set of playbooks to automate operations for a Hammerspace installation.

Each example includes a variable file, [anvil.yml](anvil.yml) which contains required
information to successfully run the playbook.

```
data_cluster_mgmt_ip: <ANVIL MGMT IP>   # management IP for Anvil
hsuser:               admin             # user with admin level role
password:             ''                # admin user password
```
### Playbooks ###
- Active Directory ( [join](ad-join.yml) / [leave](ad-leave.yml) ) - Join and leave Active Directory
- [Installation check](install-ready.yml) - Checks if an installation is ready to receive API calls.
- Anti-virus ( [add](av-add.yml) / [remove](av-remove.yml) ) - Add and remove ICAP servers used for anti-virus scanning
- License ( [add](license-add.yml) ) - Add product license
- Retention ( [add](retention-add.yml) / [remove](retention-delete.yml) ) - Add and remove retention schedules
- Schedule ( [add](schedule-add.yml) ) - Add and remove regular schedules
- Share snapshot ( [add](share-snapshot-add.yml) / [remove](share-snapshot-remove.yml) ) - Take and remove share snapshots
- Syslog ( [add](syslog-add.yml) / [remove](syslog-remove.yml) ) - Add and remove syslog servers
- Objective ( [add](objective-add.yml) / [set](objective-set.yml) ) - Add and set objectives
- Object Volume ( [add](object-storage-volume-add.yml) ) - Add object storage volumes

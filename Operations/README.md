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
- Active Directory ( [join](ad-join.yml) / [leave](ad-leave.yml) )
- [Installation check](install-ready.yml) - checks if an installation is ready to receive API calls)
- Anti-virus ( [add](av-add.yml) / [remove](av-remove.yml) )
- License ( [add](license-add.yml) )
- Retention ( [add](retention-add.yml) / [remove](retention-delete.yml) )
- Schedule ( [add](schedule-add.yml) )
- Share snapshot ( [add](share-snapshot-add.yml) / [remove](share-snapshot-remove.yml) )
- Syslog ( [add](syslog-add.yml) / [remove](syslog-remove.yml) )
- Objective ( [add](objective-add.yml) / [set](objective-set.yml) )
- Object Volume ( [add](object-storage-volume-add.yml) )

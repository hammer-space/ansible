## Common API operations for managing a Hammerspace installation

A set of playbooks to automate operations for a Hammerspace installation.

Each example includes a variable file, [anvil.yml](anvil.yml) which contains required
information to successfully run the playbook.

```
data_cluster_mgmt_ip: <ANVIL MGMT IP>   # management IP for Anvil
hsuser: admin # User with admin level role
password: ''  # admin user password
```
### Playbooks ###
- Active Directory ( join / leave )
- Installation check (checks if an installation is ready to receive API calls)
- Anti-virus ( add / remove )
- License ( add )
- Retention ( add / remove )
- Schedule ( add )
- Share snapshot ( add / remove )
- Syslog ( add / remove )
- Objective ( add / set )
- Object Volume ( add )

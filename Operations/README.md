## Common API operations for managing a Hammerspace installation

A set of playbooks to automate operations for a Hammerspace installation.
Each example includes a variable file, anvil.yml which contains required
information to successfully run the playbook.

**anvil.yml - variables used for operations**

'''
data_cluster_mgmt_ip: <ANVIL MGMT IP>   # management IP for Anvil
hsuser: admin # User with admin level role
password: ''  # admin password
addomain: mydomain.a.com   # Active Directory domain
aduser: Administrator      # Active Directory user
adpassword: ''  # Active Directory password
activationid: ''  # License key
'''

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

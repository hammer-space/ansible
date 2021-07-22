## Global File System solution for VMware

A single playbook to deploy several sites from scratch using the OVA for VCenter environments.

The playbook goes through the following steps. If one of the steps is not desired, it can easily be removed from the list.

1. Deploys Anvil + DSX nodes to represent multiple sites within a single VCenter environments
2. Applies a License
3. Configures Anti-virus
4. Joins Active Directory
5. Adds Object Storage system and a Shared Object volume
6. Adds Retention schedules
7. Adds Snapshot schedules
8. Creates a Global File system
9. Sets up Share snapshots
10. Adds objectives

The tasks to automate across the global file system have been modified slightly from the generic Operations only to accommodate for more advanced looping.

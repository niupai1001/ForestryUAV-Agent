# Windows host bridge

`server.py` is started by `setup.ps1` on loopback. It resolves explicitly
authorized Windows directories, performs bounded file reads/searches, and creates
project-owned Docker job containers. It has no endpoint for host shell execution.

Source grants are signed by the Runtime and mounted read-only. Runtime-owned chat
workspaces are the only writable mounts. Normal Python and shell jobs use Docker
network mode `none`; dependency installation mounts only the workspace dependency
directory and uses a separate networked job.

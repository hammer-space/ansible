FROM fnproject/python:3.11

WORKDIR /function

# Install dependencies
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy function handler
COPY func.py func.yaml ./

# Copy deployer module from parent project (build context must be repo root)
COPY oci_deploy.py ./

# Copy bootstrap script (used by _build_bootstrap_script)
COPY cloud-init/tier0-bootstrap.sh ./cloud-init/

ENTRYPOINT ["fdk", "func.handler"]

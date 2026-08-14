# Python SDK boundary

This package contains public transport and model types only. It does not import the server kernel. The API will expand contract-first; unstable methods remain under the `v1alpha1` namespace in their paths and models.

```python
from infra_intelligence_sdk import Client, ResourceObservation

client = Client("http://localhost:8080", tenant_id="local", actor_id="developer")
accepted = client.ingest_resource(ResourceObservation.from_dict(payload))
```


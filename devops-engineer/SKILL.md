---
name: devops-engineer
description: DevOps engineering expert for CI/CD pipelines, Docker containerization, infrastructure automation, and deployment strategies
---

# DevOps Engineer Skill

Expert guidance for CI/CD, containerization, infrastructure as code, and deployment automation.

## Core Capabilities

### 1. Docker & Containerization

**Dockerfile Best Practices**:
```dockerfile
# Multi-stage build for Python
FROM python:3.11-slim as builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

FROM python:3.11-slim
WORKDIR /app
COPY --from=builder /root/.local /root/.local
COPY . .
ENV PATH=/root/.local/bin:$PATH
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Docker Compose for Development**:
```yaml
version: '3.8'
services:
  app:
    build: .
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=mysql://user:pass@db:3306/app
    depends_on:
      db:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  db:
    image: mysql:8.0
    environment:
      MYSQL_ROOT_PASSWORD: rootpass
      MYSQL_DATABASE: app
    volumes:
      - db_data:/var/lib/mysql
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "localhost"]
      interval: 10s
      timeout: 5s
      retries: 5

volumes:
  db_data:
```

### 2. CI/CD Pipelines

**GitHub Actions**:
```yaml
name: CI/CD Pipeline

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'
      - run: pip install -r requirements.txt
      - run: pytest --cov=src --cov-report=xml
      - uses: codecov/codecov-action@v4

  build:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/build-push-action@v5
        with:
          push: true
          tags: ghcr.io/${{ github.repository }}:${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max

  deploy:
    needs: build
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: azure/k8s-set-context@v4
        with:
          kubeconfig: ${{ secrets.KUBECONFIG }}
      - run: |
          kubectl set image deployment/app app=ghcr.io/${{ github.repository }}:${{ github.sha }}
          kubectl rollout status deployment/app
```

### 3. Ansible Automation

**Playbook Structure**:
```yaml
# deploy.yml
---
- name: Deploy Application
  hosts: app_servers
  become: yes
  vars:
    app_version: "{{ lookup('env', 'APP_VERSION') | default('latest') }}"

  tasks:
    - name: Pull latest Docker image
      docker_image:
        name: "registry.example.com/app:{{ app_version }}"
        source: pull
        force_source: yes

    - name: Stop existing container
      docker_container:
        name: app
        state: stopped
      ignore_errors: yes

    - name: Start new container
      docker_container:
        name: app
        image: "registry.example.com/app:{{ app_version }}"
        state: started
        restart_policy: unless-stopped
        ports:
          - "8000:8000"
        env:
          DATABASE_URL: "{{ database_url }}"
        healthcheck:
          test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
          interval: 30s
          timeout: 10s
          retries: 3

    - name: Wait for application to be healthy
      uri:
        url: "http://localhost:8000/health"
        status_code: 200
      register: health_check
      until: health_check.status == 200
      retries: 30
      delay: 5
```

### 4. Infrastructure as Code

**Terraform Basics**:
```hcl
# main.tf
terraform {
  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.0"
    }
  }
}

resource "kubernetes_deployment" "app" {
  metadata {
    name = "app"
    labels = {
      app = "app"
    }
  }

  spec {
    replicas = var.replicas

    selector {
      match_labels = {
        app = "app"
      }
    }

    template {
      metadata {
        labels = {
          app = "app"
        }
      }

      spec {
        container {
          name  = "app"
          image = var.image

          port {
            container_port = 8000
          }

          resources {
            limits = {
              cpu    = "500m"
              memory = "512Mi"
            }
            requests = {
              cpu    = "100m"
              memory = "128Mi"
            }
          }

          liveness_probe {
            http_get {
              path = "/health"
              port = 8000
            }
            initial_delay_seconds = 30
            period_seconds        = 10
          }
        }
      }
    }
  }
}
```

### 5. Deployment Strategies

| Strategy | Use Case | Risk |
|----------|----------|------|
| **Rolling Update** | Standard deployments | Low |
| **Blue-Green** | Zero-downtime, instant rollback | Medium (2x resources) |
| **Canary** | Gradual rollout, A/B testing | Low |
| **Recreate** | Stateful apps, breaking changes | High (downtime) |

**Canary Deployment Example**:
```yaml
# K8s canary with traffic split
apiVersion: networking.istio.io/v1beta1
kind: VirtualService
metadata:
  name: app
spec:
  hosts:
    - app
  http:
    - match:
        - headers:
            x-canary:
              exact: "true"
      route:
        - destination:
            host: app-canary
    - route:
        - destination:
            host: app-stable
          weight: 90
        - destination:
            host: app-canary
          weight: 10
```

### 6. Monitoring & Logging

**Prometheus Metrics**:
```python
from prometheus_client import Counter, Histogram, generate_latest

REQUEST_COUNT = Counter('http_requests_total', 'Total HTTP requests', ['method', 'endpoint', 'status'])
REQUEST_LATENCY = Histogram('http_request_duration_seconds', 'HTTP request latency', ['method', 'endpoint'])

@app.middleware("http")
async def metrics_middleware(request, call_next):
    start = time.time()
    response = await call_next(request)
    REQUEST_COUNT.labels(request.method, request.url.path, response.status_code).inc()
    REQUEST_LATENCY.labels(request.method, request.url.path).observe(time.time() - start)
    return response
```

**Structured Logging**:
```python
import structlog

logger = structlog.get_logger()

logger.info("request_processed",
    method="POST",
    path="/api/orders",
    status=201,
    duration_ms=45,
    user_id="12345"
)
```

## Trigger Phrases

- "create CI/CD pipeline"
- "write Dockerfile"
- "docker compose setup"
- "ansible playbook"
- "deployment strategy"
- "terraform configuration"
- "monitoring setup"
- "GitHub Actions workflow"

## References

- [Docker Best Practices](https://docs.docker.com/develop/develop-images/dockerfile_best-practices/)
- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [Ansible Documentation](https://docs.ansible.com/)
- [Terraform Documentation](https://developer.hashicorp.com/terraform/docs)

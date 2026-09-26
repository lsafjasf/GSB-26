# Web service deployment pipeline
param env: enum(dev, staging, prod) = dev
param replicas: int = 1
param debug: bool = false

step checkout {
  run: "git checkout"
}

step build {
  needs: checkout
  args: replicas=2
  run: "docker build"
}

step unit_test {
  needs: build
  run: "pytest"
}

step lint {
  needs: build
  run: "flake8"
}

step package {
  needs: unit_test, lint
  run: "docker push"
}

step route {
  needs: package
  branch on env {
    case dev -> deploy_dev
    case staging -> deploy_staging
    case prod -> canary
  }
}

step deploy_dev {
  run: "deploy to dev cluster"
}

step deploy_staging {
  run: "deploy to staging cluster"
}

step canary {
  when: replicas > 1
  run: "deploy canary"
}

step promote {
  needs: canary
  run: "promote canary to full rollout"
}

step notify {
  needs: route
  run: "notify slack"
}

step placeholder {
  needs: notify
}

step report {
  needs: placeholder
  run: "write deploy report"
}

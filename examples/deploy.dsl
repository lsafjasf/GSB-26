# 部署流程示例：参数、依赖、参数引用、条件分支
param dry_run: bool = false
param channel: enum(stable, canary, nightly) = stable
param retries: int = 3

step fetch {
  args: { retries: $retries, url: "https://example.invalid/app" }
}

step lint {
  needs: fetch;
}

step unit_test {
  needs: fetch;
}

step package {
  needs: lint, unit_test;
  args: { dry: $dry_run }
}

step route {
  needs: package;
  branch on $channel {
    stable: deploy_stable, smoke_test;
    canary: deploy_canary, smoke_test;
    nightly:;
  }
}

step deploy_stable {
  needs: route;
}

step deploy_canary {
  needs: route;
}

step smoke_test {
}

step rollback {
}

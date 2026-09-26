param env: enum(dev, prod) = dev

step deploy {
  needs: package
  when: region == "cn"
  run: "deploy"
}

step route {
  branch on env {
    case dev -> deploy
    case prod -> missing_step
  }
}

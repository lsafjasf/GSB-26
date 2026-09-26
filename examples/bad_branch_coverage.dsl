param env: enum(dev, staging, prod) = dev
param region: str = "cn"

step route_env {
  branch on env {
    case dev -> a
    case prod -> a
  }
}

step route_region {
  branch on region {
    case "cn" -> a
  }
}

step a {
  run: "a"
}

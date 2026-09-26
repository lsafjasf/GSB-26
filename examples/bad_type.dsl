param replicas: int = "three"
param env: enum(dev, prod) = prod

step build {
  args: replicas="many"
  when: replicas == "2"
  run: "build"
}

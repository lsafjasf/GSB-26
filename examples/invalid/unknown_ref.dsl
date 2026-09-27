param retries: int = 3
step build {
  args: { n: $retires, missing: $ghost }
  needs: phantom;
}

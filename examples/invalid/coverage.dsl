param channel: enum(stable, canary, nightly) = stable
step route {
  branch on $channel {
    stable: deploy;
    canary: deploy;
  }
}
step deploy {
}

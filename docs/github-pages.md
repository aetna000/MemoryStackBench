# GitHub Pages Results

The simplest publishing model is:

```bash
memorybench run --target targets/mem0.yaml --suite suites/seven_sins_v0_1 --out runs/mem0-local
memorybench report --run runs/mem0-local --site site/mem0-local
memorybench leaderboard --runs runs --out site/leaderboard
```

Then configure GitHub Pages to publish the `site/` directory.

For automated runs, use GitHub Actions to:

1. install the package
2. run one or more pinned targets
3. copy each run into `site/<target-id>/`
4. generate a top-level index later
5. upload `site/` as a Pages artifact

The included workflow publishes `site/` when it exists on the default branch.

# Security policy

This repository builds https://depauwvswabash.com/, an unofficial fan site that compares DePauw University and Wabash College (not affiliated with either school). The site is static pages: it has no accounts, forms, comments or visitor data.

## Reporting a vulnerability

Please report a security problem privately, not as a public issue or pull request:

1. Open the repository's **Security** tab and choose **Report a vulnerability**, or go straight to https://github.com/jacksonmote/DePauw-vs-Wabash-web/security/advisories/new.
2. Say what you found, where, and how to reproduce it. A link or a short description is enough.

This is a volunteer project, so replies are best effort and there is no bounty. Reports are kept private until a fix is out.

## What is in scope

- The code and workflows in this repository.
- The generated site at depauwvswabash.com (for example, script injection or a link that goes somewhere it should not).

## What is not

- The two schools' own websites, or the accuracy of their records. A wrong score or a missing game is not a security problem: use the "Report a data error" issue form instead.
- Reports that need physical access to someone's device, or that only describe missing HTTP headers on GitHub Pages (the host does not allow changing them).

Only the latest version on `main` is supported.

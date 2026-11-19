# Application authentication

There are two roles: `admin` manages users and can use all agents; `user` can
use agents and approve SAP actions but cannot manage accounts or review admin
feedback. The landing page is public; agent APIs require a session.

Add to the private **root `.env`** file before deployment:

```
AUTH_ADMIN_USERNAME=your.admin
AUTH_ADMIN_PASSWORD=choose-a-unique-password-at-least-12-characters
```

Do not commit or send these values in chat. Usernames must have 3–128 letters,
digits, `.`, `_`, `@`, or `-`; passwords must have 12–256 characters. Run
`.\scripts\deploy.ps1` in PowerShell. It validates the configuration, builds
the frontend, provisions and binds PostgreSQL if necessary, and pushes the app.
It creates a temporary interpolation file for the CF command and deletes it
afterward. CF receives the bootstrap credentials as application environment
variables, which CF administrators with environment access can read. Use an
approved secrets service for production. The root `.env` is not uploaded.

The user and session tables are created at startup. The initial admin is created
only if the user table is empty; later `.env` changes do not reset passwords or
overwrite existing accounts. Use the administrator screen to create users, reset
passwords, change roles and disable accounts. Passwords use salted scrypt; opaque
session tokens are hashed in the database, expire after eight hours, and are sent
in HTTP-only, same-site cookies. Role, password or active-status changes revoke
that user's sessions. The current admin cannot disable or demote themself.

Login throttling is process-local. MFA, SSO, distributed rate limiting and
account recovery are not implemented. Protect production with an enterprise
identity provider. Chat history is intentionally shared per agent across users;
authentication does not make it private to an individual account. Historical
conversations saved before authentication are not attributed to users.

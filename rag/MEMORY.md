# Conversation memory

Deploy using `.\scripts\deploy.ps1`. It creates the `rag-memory-db` service
instance if absent, waits for provisioning, and pushes the manifest, which binds
that service. By default it uses offering `postgresql-db` and plan `trial`.
These must be available and entitled in the targeted BTP space; check using
`cf marketplace -e postgresql-db`. If a different compatible plan is available,
pass `-MemoryServicePlan <plan>` (and, if necessary, `-MemoryServiceOffering
<offering>`). Paid plans can incur costs. Existing service instances are reused.
Plain `cf push` binds the service listed in the manifest but cannot create it.

The app reads the matching `rag-memory-db` binding from `VCAP_SERVICES`, uses its
PostgreSQL `uri` without logging credentials, and requires TLS hostname and
certificate verification for the binding. If the binding provides an
`sslrootcert` PEM, it is used as the CA; otherwise the system trust store is
used. Set `RAG_MEMORY_SERVICE_NAME` if the binding name changes. The manifest sets
`RAG_MEMORY_REQUIRED=true`, so a missing or unusable database fails startup
instead of silently losing chat history. The app creates its `rag_chat_turns`
table and index at startup; the binding identity needs schema creation and
read/write privileges. Service provisioning requires adequate entitlement,
quota, permissions and a valid CF login. The exact binding and TLS behavior must
be verified with the chosen provider in the target space.

For local development or a separately managed database, an explicit
`RAG_MEMORY_DATABASE_URL` overrides binding discovery; use suitable TLS options.
When no URI or binding is configured and memory is not required, the original
main-agent in-process store remains active; EWM and migration are not persisted.
Do not use SQLite for durable Cloud Foundry storage: container files are ephemeral.
For local development only, set `RAG_MEMORY_DATABASE_URL=sqlite:///rag-memory.sqlite3`.
This file contains chat text and should never be committed or uploaded.

IBP, S/4 capacity, maintenance and scenario responses are stored by the server.
EWM and migration exchanges are submitted by their browser screens after each
exchange; failures, closing the page, or network interruption can prevent those
submissions. Previously held in-memory conversations and browser-local chat
history are not imported. Only turns completed after configuration are stored.
Recent turns are loaded by conversation ID and agent for ongoing context. For
the four main chat agents, up to two matching archived turns from the same agent
(including other conversations) are retrieved by keyword as untrusted historical
context. All users share this history because the app has no user authentication.
Other agents' histories are not searched. Matching scans all saved turns for the
agent and is intended for a small pilot; an indexed search is needed at scale.
Requests for the previous conversation select the latest other conversation for
the same agent and include its last four exchanges; long answers retain their
beginning and end. With no authentication, "previous" is the latest conversation
from any user of that agent, not necessarily the requester's previous session.
This does not train a model. EWM and migration
memory is capture only and is not yet used for their context. Conversation IDs
are not authentication. The application does not currently identify users or
enforce ownership of conversations, so protect access at the application or
platform boundary before using this for sensitive conversations. Configure
backup, retention and deletion policies in the chosen database.

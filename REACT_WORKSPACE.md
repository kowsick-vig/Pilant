# Pilant App Studio

Pilant turns a user's connected software and existing data into a dedicated app
for the way that person wants to work.

The product flow is **choose software → connect account → describe the job → use
that generated app**. The landing page is an app launcher. It does not aggregate
records from all connected tools into a shared work dashboard.

## Run

```sh
./start_workspace.sh
```

Open **http://127.0.0.1:5010**. Existing accounts created in the earlier React
version still work. New users choose **Create your account** and set a username
and password of at least 10 characters. The original Flask Studio remains at
port 5008 and is independent of this app.

The compiled frontend is included. The launcher uses this copy's Python executable
directly without activating the old copied virtual environment. If unavailable:

```sh
python3 -m venv .react-venv
.react-venv/bin/python -m pip install flask cryptography anthropic
.react-venv/bin/python -B workspace_api.py
```

## Build an app

1. Click **Build an app**.
2. Choose one software source and connect your own account. Jira and Helpdesk are
   clearly labeled sample demonstrations; Gmail, GitHub, and Slack use live APIs.
3. Give your app a name and describe the work you want to do.
4. Pilant reads the source's available data, composes the app, and opens it.

Each app has its own title, description, navigation, screens, column choices,
filters, density, accent, saved configuration, and URL (`/#/app/<id>`). There is
no global connector sidebar or generic layout selector inside the generated app.
Each page inherits the app's single source binding, enforced by the backend.

Examples of user goals:

- Gmail: “I manage customer email. Give me a focused reply inbox, a follow-up
  screen, and a sent-mail view.”
- Jira: “I lead delivery. Show blocked work first, then a sprint board and a
  compact issue table showing owners and priorities.”
- Helpdesk: “Help me work through escalated tickets one at a time, with a second
  screen to browse all requests.”

Users can create multiple different apps for the same software. Their screens
reference the same underlying data rather than creating copies of records. Each
user's apps, connections, and sample-record edits remain private to that user.

**Adapt this app** accepts a refinement request and regenerates that app's
configuration while retaining its data source. If refinement fails, the saved app
is preserved. The app launcher is only a place to create and reopen apps.

## Dynamic composition

`app_composer.py` asks Claude to compose 1–6 purposeful screens using the user's
request, working role, and a profile of the actual returned data. The profile
includes available normalized fields and real statuses, not email/message body
text. Claude chooses navigation titles, screen layouts, filters, columns, density,
and accent. The source binding is supplied and enforced by the server.

The current functional layout vocabulary is inbox, board, feed, table, and focus.
The React renderer implements the associated interactions. The model does not
emit arbitrary executable React code or fabricate the records shown in screens.
A working Anthropic API key (`ANTHROPIC_API_KEY` in `.env`) is required for AI
composition and refinement. If initial AI composition is unavailable, the app is
explicitly labeled **Starter interface** and uses a source-specific fallback.
That fallback is not presented as a fully AI-designed app. Refinement never
silently replaces an existing app with a starter template.

## Supported source behavior

- **Gmail:** inbox/sent/starred/all-mail views, search, full text reading, star,
  mark-read, archive, and reply with an explicit review/send step.
- **GitHub:** existing repository issues; read-only in this version, with links
  back to the source.
- **Slack:** recent messages in one configured channel; read-only with source
  links. User IDs are not resolved into profiles; threads are not expanded.
- **Jira / Helpdesk:** existing sample fixtures. Status changes persist per user
  without mutating the shared fixture lists.

Gmail fetches up to 30 matching messages; GitHub and Slack fetch up to 100 recent
records. Counts describe the returned set. Non-Gmail search filters that set;
there is no full-history pagination in this version. Calendar, arbitrary custom
widgets, attachments UI, and additional software adapters remain future work.

## Connect a live account

Connections appear during app setup and under the app's **Powered by …** control.
No existing global Gmail refresh token, GitHub token, or Slack token from `.env`
is automatically assigned to new users.

For Gmail, configure `GMAIL_CLIENT_ID` and `GMAIL_CLIENT_SECRET` as the OAuth web
application credentials and register this exact callback in Google Cloud:

```
http://127.0.0.1:5010/api/oauth/gmail/callback
```

Keep the old Studio callback registered if you use it. The flow requests
`gmail.readonly`, `gmail.send`, and `gmail.modify`. Google consent/test-user
settings must allow the connecting account. The app creation draft survives the
OAuth redirect in that user's browser session storage.

For GitHub, supply `owner/repository` and an optional token for public repositories;
private repositories require a personal token with metadata/issue read access.
For Slack, supply a channel ID and bot token with membership and history access
for that channel. Access is validated before these connections are stored.

## Storage and migration

`.workspace/workspace.sqlite3` contains account password hashes, encrypted
connections, private sample changes, and an `apps` table with owner-bound app
specifications. Existing session and encryption keys are reused. The previous
React workspace's `views` table is preserved but those combined views are not
silently converted into dedicated apps. Build a new app from the launcher.

All app open, data, refinement, and deletion endpoints check the authenticated
owner. Data requests cannot override the source configured for the app. CSRF
protection and encrypted connection storage from the earlier version remain.
A working role personalizes the interface; it is not an authorization role.

Back up the database and both key files together. Keep `.workspace/` out of version
control. The local encryption key sits beside the database, so protection of the
host matters. This remains a local prototype; organizational SSO/RBAC, managed
secrets, production deployment, password recovery, and audit logging are not built.

## Development and verification

```sh
cd frontend
npm ci
npm run dev
npm run build
```

Vite proxies `/api` to port 5010. Use the built app on that port for Gmail OAuth.
The frontend uses React, TypeScript, Vite, Lucide, and application-specific CSS.

```sh
venv/bin/python -B -m unittest discover -s tests -p test_workspace_api.py
venv/bin/python -B -m unittest discover -s tests -p test_dedicated_apps.py
cd frontend
npx playwright install chromium
npm run test:e2e
```

Start the server before browser tests; they create disposable local accounts and
apps. Tests cover private persistence and actions, ownership, source binding,
real-data filters, AI profile composition with a mocked provider, invalid schema,
connection requirements, multiple separate apps, and mobile app navigation.
They do not send real email or verify live OAuth/provider credentials.

Environment overrides: `PILANT_WORKSPACE_DATA`, `WORKSPACE_PORT`,
`WORKSPACE_GMAIL_REDIRECT_URI`. Browser tests support `PILANT_E2E_URL`.

## Jira Pilant Copilot

Open any Jira app and choose Pilant Copilot. Ask for issues by owner, status, priority, sprint, due date, or issue key; request counts and assignee breakdowns. Results appear as usable records in the app, with existing record details and status controls. Follow-ups retain the prior query context. Conversations are private to each user and app.

Jira currently uses sample data: the original 19 issues plus 12 fictional launch issues (PIL-101 through PIL-112), including blockers, dates, story points, and unassigned work. Try “Show blocked issues assigned to Priya”, then “Only the highest priority ones”, or “Show PIL-104 and why it is blocked”.

With the configured Anthropic key, the copilot translates natural language into validated queries. Counts, matching records and blocker text come from the dataset. Without AI availability, supported basic filters remain usable and are labelled Basic filters. Unsupported questions ask for clarification. Copilot questions are read-only; explicit record controls handle edits. This demo does not connect to a live Jira account.

Verification: `python -B -m unittest discover -s tests` includes query validation, exact results, follow-ups, ownership and source scope. Frontend `npm run test:e2e` includes desktop and mobile copilot flows.

## Copilot across connected apps

Pilant Copilot is now part of the shared generated-app shell, so existing and newly built apps receive it automatically. The API resolves the app’s source and reads only the authenticated owner’s adapter. GitHub, Gmail, Slack, Jira and Helpdesk all use `app_copilot.py`; additional registered adapters can use its normalized record fields without adding a separate copilot interface. `jira_copilot.py` remains a compatibility import.

Results distinguish connected data from sample data and display retrieval scope. Current adapters retrieve up to 100 recent GitHub/Slack records or 30 Gmail messages across mail folders. Counts apply only to retrieved records, not an entire remote account. Connector failures surface as errors; they do not substitute sample records. Conversation history remains scoped to user and app.

Cross-source API tests mock adapter responses to verify source/owner binding and failure handling; browser tests exercise real sample Helpdesk queries and live-source interface variants. They do not require access to users’ external accounts.

## Conversational app builder

After selecting connected software, step two is a Pilant AI conversation. Users describe their goal, answer contextual follow-up questions, or choose suggested replies. A cumulative app brief and editable name appear beside the chat. Build my app sends that brief to the existing validated app composer. Users can build after the first reply or keep refining.

The new `/api/apps/design` endpoint requires authentication, CSRF and the owner's source connection. AI receives a record-field/status profile, not record text or credentials. Provider failures use clearly labelled guided questions; requested preferences are preserved. Draft conversations persist in per-user session storage; changing software resets the brief and conversation. Existing apps and their Copilots are unaffected.

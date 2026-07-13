# Gateway PCC — User Manual

## Adding a Client, a Facility, and Importing a 2FA QR Code

This guide walks you through setting up your facilities in Gateway PCC so you can
launch and auto-sign-in to PointClickCare (PCC) from the desktop app.

The order is always:

1. **Create a Client** (the organisation / company)
2. **Add a Facility** under that client (the individual site + its PCC login)
3. **Import the facility's QR code** (its PointClickCare two‑factor secret)

> **Note on the two different QR codes**
> - When **you** sign in to *Gateway PCC itself*, you scan a QR with **Google
>   Authenticator** (this is your personal login 2FA — set up once).
> - The QR described in this manual is **different**: it is the **facility's
>   PointClickCare 2FA secret**, which Gateway PCC stores so the desktop app can
>   type the 6‑digit PCC code for you at launch. You do *not* scan it with your
>   phone — you upload the image file into Gateway PCC.

---

## Before you start

- Sign in to the Gateway PCC website with your username, password, and your
  Google Authenticator code.
- Open **⚙ Settings** (button at the bottom-left of the sidebar). Everything in
  this guide happens on the Settings screen.

---

## Step 1 — Create a Client

A **client** is the top-level organisation. Facilities live underneath it.

1. On the **Settings** screen, scroll to the **Clients** card.
2. In the **New client name** box, type the client's name (e.g. `Sunrise Care Group`).
3. Click **+ Create Client**.

The client now appears in the left sidebar tree and in the **Client** dropdown of
the *Add Facility* form below.

> **Shortcut:** You can skip this step and create the client while adding the
> facility — see the *"…or type a new client"* field in Step 2.

---

## Step 2 — Add a Facility

A **facility** is a single site with its own PointClickCare login. Scroll to the
**Add Facility** card on the Settings screen.

### 2a. Choose the client

- Pick the client from the **Client** dropdown, **or**
- Type a brand-new client name in **…or type a new client** — it will be created
  automatically when you save the facility.

### 2b. Fill in the facility details

| Field | What to enter |
|-------|----------------|
| **Facility name** *(required)* | The site name, e.g. `North Campus` |
| **Location** | City/state, e.g. `Chicago, IL` (optional) |
| **Site URL** | Leave blank to use the default PCC login site from Settings, or enter a specific URL |
| **Username** | The facility's PointClickCare username |
| **Password** | The facility's PointClickCare password |

> **Tip — copy from an existing facility:** Use **Copy details from** at the top
> of the card to clone another facility's client, name, location, URL and
> selectors. It deliberately does **not** copy the username, password, or QR — so
> you only fill in the new login's specifics.

### 2c. (Don't click Add yet)

Before saving, import the facility's PCC two‑factor QR code — see Step 3. You can
then click **+ Add Facility** once.

---

## Step 3 — Import the QR Code (PCC two-factor secret)

The **Two-Factor (TOTP)** block is in the middle of the *Add Facility* card. It
has two tabs:

- **Import QR code** — upload a QR image (most common)
- **Paste hash / secret** — paste the raw secret text instead

### Option A — Import a QR image (recommended)

1. Make sure the **Import QR code** tab is selected.
2. Click **Choose File** and select the QR code **image** (a `.png`, `.jpg`, or a
   screenshot of the PCC 2FA QR).
3. Click **Decode QR**.
4. You should see **"Secret captured ✓"**, and a **live 6-digit code** appears in
   the *Current 6-digit code* box with a countdown timer.

✅ Seeing a live code that refreshes every ~30 seconds means the secret is valid
and stored correctly.

### Option B — Paste the secret instead

If you have the secret as text rather than an image:

1. Click the **Paste hash / secret** tab.
2. Paste the base32 secret (Gateway PCC also accepts a full
   `otpauth://...` URL or a spaced/hyphenated secret — it cleans it up for you).
3. Click **Verify**.
4. Confirm the **live 6-digit code** appears.

---

## Step 4 — Save the Facility

1. Click **+ Add Facility**.
2. You'll see **"Saved ✓"** and Gateway PCC jumps straight to the new facility's
   detail screen.
3. On that screen, the **TOTP / 2FA** row shows **"Configured ✓ — current code …"**,
   confirming the secret is saved and working.

The facility now appears under its client in the left sidebar.

---

## Verifying it all worked

On the facility's detail page you should see:

- **Location**, **Site URL**, **Username** filled in
- **TOTP / 2FA: Configured ✓** with a live, refreshing 6-digit code

If the TOTP row instead says **"⚠ Stored secret is not valid — re-add the QR/hash"**,
go back to **Edit** and re-import the QR or re-paste the secret.

---

## Launching the facility

Adding and managing facilities happens on the **website**. **Launching and
auto-signing-in** happens in the **desktop app**:

1. Open the Gateway PCC **desktop app** and sign in (same username, password, and
   Google Authenticator code).
2. Find the facility in the sidebar (or use the search box).
3. Click **Launch** — the app opens Firefox (through the US proxy if enabled),
   fills in the username and password, and types the current PCC 6‑digit code for
   you automatically.

---

## Quick reference

| I want to… | Where |
|------------|-------|
| Create a client | Settings → **Clients** → *+ Create Client* |
| Add a facility | Settings → **Add Facility** |
| Create a client *while* adding a facility | *…or type a new client* field |
| Import the PCC QR | Add Facility → **Import QR code** tab → *Decode QR* |
| Paste the PCC secret instead | Add Facility → **Paste hash / secret** tab → *Verify* |
| Copy settings from another facility | Add Facility → **Copy details from** |
| Edit / delete a facility | Click the facility in the sidebar → **Edit** / **Delete** |
| Launch & sign in | **Desktop app** → pick facility → **Launch** |

---

## Troubleshooting

- **"Could not read a QR code from that image."** — The image is too small,
  blurry, or cropped. Use a clearer, larger screenshot of the full QR (including
  the white border around it) and try **Decode QR** again.
- **"Not a valid authenticator secret."** — The pasted text isn't a valid
  base32 secret. Use the QR image instead, or paste the full `otpauth://` URL.
- **No live code appears after decoding** — Re-select the file and click
  **Decode QR** again; confirm you picked an image file, not a PDF.
- **Facility name is required** — You must enter a facility name before saving.
- **Forgot to pick a client** — Choose one from the dropdown or type a new client
  name, then click **+ Add Facility** again.

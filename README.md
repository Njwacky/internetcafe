# Café Desk — Local-First Desktop Document Studio (`v0.7.0`)

A local-first desktop application for internet cafés, copy shops, and walk-in service desks, written in Python with CustomTkinter. Staff and customers use the same workspace; administrative settings, sales reports, recovery copies, and optional AI polishing require a staff PIN. For shared walk-in computers, always run Café Desk inside a standard (non-Administrator) operating-system user account.

## Features

- **PDFs:** merge, visually preview and select pages to extract, extract typed page ranges, and split every page. Existing split-page files are not overwritten. Includes an optional **Paid Add-on: Remove PDF password** (`CDADD1...` key signed by the owner for that computer's `Machine ID`) to unlock and save password-free copies of customer PDFs (e.g. bank statements and payslips) once the customer supplies the open password.
- **Images and scanning:** on Windows, find and select a WIA-compatible scanner and scan one page at a time inside the app; preview pages and save them as a single PDF. You can also launch a separate scanner app and import scans. Convert JPG, PNG, BMP and TIFF images to PDF, including multi-page TIFF.
- **Bluetooth files (Windows):** a shortcut opens Windows Bluetooth settings and the Bluetooth File Transfer window. After receiving a file in Windows, select it to add a PDF or image to the appropriate app tool. The app does not receive files automatically.
- **English OCR:** select one or more images, preview them inside the app with an animated scanner-line effect (plus a larger preview option), and extract editable English text using a separately installed Tesseract OCR engine. **Help & privacy** includes a clickable direct link to download Tesseract OCR. Save the result as text. OCR on PDF files is not included. Check the result carefully; OCR makes mistakes.
- **Career documents:** 13 CV designs and 10 options each for cover letters, professional summaries and introductions. General job seekers can leave `Desired role` blank (and `Company` is hidden automatically on CVs and summaries) for clean, placeholder-free wording. CVs have 13 flowing PDF designs with distinctive headers, section styles, typography and page accents that flow automatically onto additional pages with page numbers. `Document type` and `Template` sit right above the live side-by-side **A4 PDF preview** (with an enlarge icon button to inspect the A4 page in a large modal window) before exporting.
- **Cards & invitations:** create printable **Business cards** (`10 per A4 sheet 85 × 55 mm`, `8 per A4 sheet 90 × 50 mm`, or single card proof) and **Invitation cards** (`2 per A4 sheet A5 landscape 148 × 105 mm`, `4 per A4 sheet A6 postcard`, or full-page A4 invitation) across **all 13 templates** (`Classic` through `Compact Profile`), with optional logo/image, optional trim/crop marks, live single-card or A4-sheet preview, enlarge modal, PDF export, and direct `Add to PDF merge`.
- **Forms creator:** build printable and digitally fillable **AcroForm** A4 PDFs — *General Application Form*, *Affidavit / Sworn Declaration*, *Consent / Authorization Form*, *Customer / Walk-in Intake Form*, or custom forms with custom text, multiline, checkbox, date and signature fields, live A4 preview, enlarge modal, and direct `Add to PDF merge`.
- **A4 picture sheets:** arrange, crop, rotate, duplicate and resize individual picture frames (in millimetres) with a side-by-side layout that keeps picture tools right beside the A4 page canvas. In `Fill & crop` mode, cropped-out pixels are physically trimmed before PDF embedding so hidden image regions cannot be extracted from exported PDFs.
- **Branding:** choose one of 10 icons or upload a PNG/JPG/BMP logo. The logo is copied and resized locally, and appears in the app sidebar; it is not automatically added to documents or receipts.
- **Public price list and simple receipts:** the service list starts **empty**. Staff can enter each company's own services, prices and currency symbol/code. Anyone using the app can build a receipt from saved prices. Receipt PDFs show line items and totals. Staff may explicitly record a sale in local history, void mistaken entries with an audit reason (preserving the original record), back up the SQLite database online, view date-filtered net totals, export formula-safe CSV, or save a selected sale-record PDF. **Important:** Sales history is an internal desk log only — it is **not** a fiscal POS or tax register, does not calculate VAT/tax, collect payment, reconcile cash drawers, or establish legal tax-invoice compliance.
- **Staff PIN & hardened app-data protection:** business settings, price settings, sales reports, recording/voiding a sale, AI settings, recovery project opening, and use of AI are gated by a 6–12 digit local PIN (PBKDF2-HMAC-SHA256). Five failed attempts trigger a 60-second cooldown persisted on disk across app restarts, and damaged/corrupted `settings.json` files fail closed (never silently resetting PIN protection), quarantine the damaged file as `settings.json.corrupt`, and allow verified restoration from `settings.json.bak`. Public exports, PDF/image/OCR inputs, and project opens are blocked from overwriting or reading inside the internal Café Desk configuration/recovery/sales directory.
- **Bundled Unicode PDF fonts:** DejaVu Sans & Serif TrueType fonts are bundled in `assets/fonts/` and registered automatically so accented Latin, African, Cyrillic, Greek, and currency glyphs render cleanly in exported PDFs without tofu boxes.
- **Optional AI:** a staff member may enter a **Google Gemini** (`AIza...` from `https://aistudio.google.com/api-keys`), **OpenRouter** (`sk-or-...`), **OpenAI** (`sk-...`), **Groq** (`gsk_...`), **DeepSeek**, or custom OpenAI-compatible API key for the session and improve a draft. Auto-detect automatically routes `AIza...` keys to Google Gemini (`gemini-2.5-flash`) and `sk-or-...` keys to OpenRouter, and **AI settings** includes a one-click link to Google AI Studio plus a **⚡ Test AI connection** button. A confirmation is required before the entire draft is sent; usage may be billed. Check generated content.
- **About & contact:** dedicated **About & contact** screen in the sidebar with app version details, 1-year license status, direct WhatsApp support & licensing (`072 227 9505` / `https://wa.me/27722279505`), developer email (`njwayelodlamini@gmail.com`), and GitHub profile & repository links (`https://github.com/Njwacky`, `https://github.com/Njwacky/internetcafe`) with one-click Open and Copy buttons.

## Setup and run

1. Install Python **3.11 or newer** (required for `Pillow>=13.0.0` security fixes), including Tkinter. On Windows, install Python from python.org and select **Add Python to PATH**. Linux may require `python3-tk`; use a macOS Python installation that includes Tkinter.
2. Unzip or clone this folder and open a terminal in it.
3. Create and activate a virtual environment:
   - Windows Command Prompt: `py -m venv .venv` then `.venv\Scripts\activate.bat`
   - Windows PowerShell: `py -m venv .venv` then `.venv\Scripts\Activate.ps1`
   - macOS/Linux: `python3 -m venv .venv` then `source .venv/bin/activate`
4. Install packages: `python -m pip install -r requirements.txt` (or `python -m pip install -r requirements-lock.txt` for exact pinned versions).
5. Start: `python app.py`

This is desktop source code, not a phone app or a ready-to-run installer. Repeat setup on each computer, or build a portable copy using the instructions below.

## OCR setup

Open **Help & privacy** inside Café Desk and click **Download Tesseract OCR (Windows .exe)** or **Open Tesseract downloads page** (`https://github.com/UB-Mannheim/tesseract/wiki`) to install the **Tesseract OCR** program separately, keeping its English (`eng`) language data ticked, on each computer where OCR is used. Make sure `tesseract` is on the system PATH, then restart Café Desk. On Windows, the app also checks standard `C:\Program Files\Tesseract-OCR\tesseract.exe`, `C:\Program Files (x86)\Tesseract-OCR\tesseract.exe`, and `%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe` locations. Alternatively set `TESSERACT_CMD` to the full path of the executable before launching the app. OCR runs locally and does not send images to a server. Tesseract is not bundled with the app or build scripts.

## Staff access and privacy

The first person to open Business settings, Price settings or AI settings chooses the PIN; set it yourself before customers use the app. The PIN verifier is salted and hashed locally. The PIN protects normal app navigation, not the underlying computer: someone with administrator or OS-level access to local files can modify or bypass it. Five failed attempts lock staff entry for 60 seconds and persist the cooldown across app restarts; if `settings.json` is damaged, staff access fails closed until an administrator restores `settings.json.bak` or deletes the damaged file. For a shared walk-in computer, run Café Desk inside a standard (non-Administrator) OS user account with appropriate file permissions.

The app starts locked. Staff access locks after approximately five minutes without mouse or keyboard activity; **Lock staff** also locks immediately. Locking clears the session API key. **Next customer** clears the current drafts, project photos, picture sheet, OCR text, file lists, receipt items, temporary merge files, opt-in recovery copies, session API key and staff access; it does not securely erase memory or delete saved files. Business name, icon/logo, price list, PIN verifier and recorded sales remain in the current OS user's local files. These are not stored on the USB and do not automatically sync to other computers or profiles.

If the PIN is forgotten, there is no built-in recovery. An administrator can reset the PIN by deleting the local `CafeDesk/settings.json` file, but this also resets that user's saved business name, icon selection and prices. Do not delete the entire folder: it also contains the sales database. Back up the folder first. Locations: Windows `%APPDATA%\CafeDesk`, macOS `~/Library/Application Support/CafeDesk`, and Linux `~/.config/CafeDesk` (or `$XDG_CONFIG_HOME/CafeDesk`). Saved PDFs and other documents are separate and are not deleted by this reset.

An API key is held in memory only; it is not encrypted or persisted. Do not unlock staff access or leave a key in an unattended customer session. AI sends the entire editor text to your chosen AI provider (Google Gemini, OpenRouter, OpenAI, Groq, DeepSeek, or custom endpoint) after confirmation. Obtain customer permission and review provider policies and charges. Scanner and saved-file usage can leave copies in other applications and recent-file lists.

### Anonymous usage statistics (opt-in, off by default)

Café Desk asks **once** on the Home screen whether it may send anonymous usage statistics, and the answer is **No unless you explicitly agree**. You can change it at any time under **Help & privacy → Anonymous usage statistics**. When it is on, Café Desk uploads only:

- this computer's Machine ID (`CD-XXXX-XXXX-XXXX`), the **business/shop name**, the **station name**, and the **city label** you typed in settings;
- the app version, the operating system, and a count of how many times each sidebar screen was opened.

It never uploads document text, filenames, images, scanned pages, OCR text, customer names, ID numbers, or sales/receipt figures. Turning the switch off stops all uploads immediately and also stops the online station heartbeat; licensing, updates and key sync keep working. Because the business/town labels are sent with a machine identifier, treat this as personal information under POPIA and only enable it with the shop owner's agreement.

## Scan directly in the app (Windows)

1. Install the scanner driver for your device, connect its USB cable and switch it on. The Epson EcoTank L3251 is an intended test device, but this feature **has not been tested on that device or on Windows**.
2. Open **Scan & images**, choose **Find / refresh scanners**, then select the scanner from the list. This shows scanners exposed through Windows Image Acquisition (WIA), including compatible USB or network devices. It is not a list of all printers; a printer-only or TWAIN-only driver will not appear. Having Epson Scan 2 installed does not by itself guarantee WIA access.
3. Put a page on the glass and click **Scan one page**. Wait for the operation to finish. The app requests a BMP scan using the driver's default settings; colour, resolution, source and supported format depend on the driver. It does not provide a separate Epson settings dialog.
4. Preview a scanned page, place the next page on the glass and scan again. Select **Save scanned pages as PDF** to combine scans in order. The same temporary scans are also added to the Images to PDF list. You can remove a selected scan or add other image files there. Use OCR separately if needed.
5. Scanned pages are unencrypted temporary files in the session directory. **Next customer** and normal exit attempt to remove them; an unexpected crash can leave files. The saved PDF is separate and is not deleted by **Next customer**.

If nothing is listed, verify power, USB connection, the scanner driver and the Windows Image Acquisition service; refresh the list. If the scanner appears but acquisition fails, verify scanning in Epson Scan 2 and check whether the WIA driver supports BMP transfer. Use **Open scanner app** and import its saved output as a fallback. The implementation uses `pywin32` on Windows; native packaging remains untested. A scan can take time and the app currently cannot cancel a stuck driver operation.

On macOS/Linux, direct in-app scanning is not implemented. The separate scanner button attempts to launch Image Capture or Linux Document Scanner (`simple-scan`); use that app to scan and import saved files. Device compatibility depends on the driver and system.


## Share to other computers via USB (no Python needed on target PCs)

You have **two ways** to get a standalone USB copy of Café Desk that runs on other 64-bit Windows 10/11 computers without installing Python:

### Option A — Download the pre-built USB ZIP from GitHub Actions (easiest)
1. Open the **Actions** tab on GitHub (`Build USB Portable & Installer Packages`).
2. Open the latest green workflow run and download the **`CafeDesk-Windows-USB-and-Installer`** artifact.
3. Extract **`CafeDesk-Windows-USB-Portable.zip`** and copy the entire **`CafeDesk`** folder (keep `_internal` inside it!) onto your USB flash drive.
4. Plug the USB drive into any Windows 10/11 PC and run **`CafeDesk.exe`** (or copy the `CafeDesk` folder onto that PC's Desktop for faster startup).
   - *Also included in the artifact:* **`CafeDesk-Setup.exe`** if you prefer a standard Windows Desktop/Start-Menu installer for each café PC.

### Option B — Build a USB-ready copy locally
Python 3.11+ and internet access are needed only on the build computer:

- **Windows:** Run `packaging\build-windows.bat`. Copy the entire resulting **`dist\CafeDesk`** folder (including `_internal`, `HOW-TO-RUN-FROM-USB.txt`, and `Enable-USB-Portable-Data.bat`) onto a USB drive and run `CafeDesk.exe` on target Windows PCs. To make a single-file installer, install Inno Setup 6 and compile `packaging\windows-installer.iss` (`installer-output\CafeDesk-Setup.exe`).
- **macOS/Linux:** Run `sh packaging/build-unix.sh` on the corresponding system. Copy the entire `dist/CafeDesk.app` bundle (macOS) or `dist/CafeDesk` folder (Linux) to USB.

### Portable USB Settings & Prices vs. Per-PC Mode
- **Default (Per-PC mode):** When you copy `CafeDesk` onto several café computers, each computer stores its own business name, logo, price list, PIN, and sales log in `%APPDATA%\CafeDesk`.
- **Portable USB Data mode (carry prices & settings on the USB stick):** Double-click **`Enable-USB-Portable-Data.bat`** inside the `CafeDesk` folder (or create a subfolder named **`CafeDeskData`** right next to `CafeDesk.exe`). When `CafeDeskData` exists beside `CafeDesk.exe`, all settings, logos, prices, recovery files, and sales stay **inside that USB folder** across computers.
- **Optional USB OCR (`Tesseract-OCR`):** Either install Tesseract OCR on target PCs via the direct link in **Help & privacy**, or copy a `Tesseract-OCR` folder (with `tesseract.exe` and English `tessdata`) directly inside `CafeDesk\Tesseract-OCR` on the USB stick.

Unsigned Windows/macOS builds can show SmartScreen/Gatekeeper prompts (`More info` → `Run anyway` on Windows); wider distribution may use code signing.

## Commercial Per-Computer Locking & 1-Year Licensing (`v0.7.0`)

Packaged USB / installer builds (`CafeDesk.exe`) automatically enforce **Method 1 — Offline Hardware Machine-ID + 1-Year Ed25519 Signed Activation**:

1. **One-time Owner Keypair Setup (on YOUR private PC only):**
   ```bash
   python admin_keygen.py --init-keys --apply-public-key
   ```
   - Generates your secret private signing key in `owner-keys/cafedesk_owner.private.key` and the matching public key in `owner-keys/cafedesk_owner.public.txt` (**the whole `owner-keys/` folder is gitignored — NEVER share it, and never copy it or `admin_keygen.py` onto a customer USB!**).
   - Writes only your Ed25519 **public key** into `license_guard.py`. Commit that change.
   - `packaging\build-windows.bat` and `packaging/build-unix.sh` now **refuse to build** while the demo key is still embedded. For a throw-away test build only, set `CAFE_DESK_ALLOW_DEMO_KEY=1`.

   **Then add the GitHub Actions secret (required for release builds):**

   1. Print the public key (from your private laptop, inside the repo folder):
      ```bash
      cat owner-keys/cafedesk_owner.public.txt      # macOS / Linux
      type owner-keys\cafedesk_owner.public.txt     # Windows
      ```
      It is a short one-line string such as `tuAEglg-NweqdIgh4Jbojl4yQW4iWMOd3fLEbLMV7lI`. **This is the PUBLIC half and is safe to paste.** Never use the contents of `cafedesk_owner.private.key`.
   2. On GitHub open **Settings → Secrets and variables → Actions** (the repository's own settings, not your account settings).
   3. Click **New repository secret**.
   4. **Name:** `CAFE_DESK_PUBLIC_KEY` — exact spelling, upper case, underscores.
   5. **Secret:** paste the public key line, with no quotes and no trailing spaces.
   6. Click **Add secret**. Then re-run the **Build USB Portable & Installer Packages** workflow.

   The release job then overwrites the embedded key with the secret before packaging and re-checks it, so a mismatched or missing secret cannot silently ship a demo-key build. `.github/check_release_key.py` is the guard: **release builds (push to `main`, `v*` tags, manual runs) fail closed** when the secret is missing or the demo key is embedded, while **pull-request builds only warn** — those artifacts are throw-away and must never be handed to a customer.

   **Verify what you actually published** (do this after a release, on the file you downloaded from the Releases page):
   ```bash
   python tools/verify_release_key.py CafeDesk-Windows-USB-Portable.zip
   ```
   It scans the artifact's own bytes and reports `PASS: this artifact embeds YOUR owner public key` or `FAIL: the DEMO signing key is embedded in this file`. Build-time guards cannot catch a mis-pasted secret that happens to be a different valid 32-byte value; this reads the shipped file itself. Add `--expect <key>` when `owner-keys/` is not present on that machine.

   *Why the demo key must never ship:* it is a placeholder that lives in this repository. The matching **private** half was never committed, so a stranger cannot forge a licence from it today — but you have no control over who holds it, you cannot revoke or rotate it away from copies already distributed, and anyone who ever obtained it (an old contractor, a leaked backup) could issue themselves keys you can never distinguish from your own. A build signed with your own keypair is the only one you control.

   - *Tip for commercial sales:* Set your GitHub repository visibility to **Private** in GitHub Settings so buyers cannot download raw Python source code.

2. **Installing & Activating on a Customer's Computer:**
   - Copy `dist\CafeDesk` (or `CafeDesk-Setup.exe`) from your USB onto the customer's PC and launch `CafeDesk.exe`.
   - Café Desk displays that computer's unique hardware **Machine ID** (`CD-XXXX-XXXX-XXXX`) and blocks access until activated.
   - On **your** laptop, generate a 1-year (365-day) license bound to that exact `Machine ID`:
     ```bash
     python admin_keygen.py --machine-id CD-8F3A-91BC-204E --customer "Benoni Cyber Cafe" --days 365
     ```
     *(Or simply run `python admin_keygen.py` with no arguments for the interactive prompt, which also saves a `.cafelicense` file in `owner-keys/issued-files/`.)*
   - Paste the `CDLIC1...` activation key (or load the `.cafelicense` file) into the customer's Activation window.

3. **What Happens If Someone Copies the App to Another PC or After 1 Year?**
   - **Copied to another PC:** `license.json` is stored per-PC (`%APPDATA%\CafeDesk\license.json`, never shared via `CafeDeskData`), and even if someone copies `license.json` manually, the second computer's hardware `Machine ID` will not match — Café Desk immediately blocks startup and asks for a new key from you.
   - **After 365 days (1 year):** Café Desk shows that the 1-year license expired (with clock-rollback tamper detection) and prompts the customer to contact you with their `Machine ID` for a renewal key. Staff can also check remaining days or enter a renewal key early under **About & contact**.

## Tests and limitations

Run `python -m unittest -v test_services` after installing dependencies. Automated tests cover templates, PDF operations and layout text, image conversion, custom photo size and rotation, portable projects, archive validation, inactivity timing, logo normalization, prices, sales records and CSV export, local PIN verifier, and a **mocked** OCR invocation. The GUI, actual Tesseract recognition, Windows WIA/pywin32 integration, Epson scanner hardware, real AI calls and target-platform installers have not been tested in this environment. Scanner automated tests use mock devices. Built-in PDF fonts may not render some non-Latin characters. Keep originals, check exported files, and test thoroughly before using with real customers.

## Optional CV profile photo

In **Career documents**, select **Choose CV photo** and choose a PNG, JPG, BMP or TIFF image. The photo is separate from the A4 picture sheet. When a CV PDF is saved, the photo appears in the CV header, cropped to a square. It is optional and is used for CV PDFs only—not cover letters, summaries, introductions or text exports. The text editor does not display the photo. Remove it with **Remove photo**, or clear the customer workspace between customers. The application references the original file until the PDF is exported, so keep the file in place. Consider whether a CV photo is appropriate for the customer's application and privacy needs.

## A4 picture sheets

Open **A4 picture sheets** and add images from the computer. Choose 2, 4, 6 or 9 pictures per portrait A4 page, or **Free layout** (starts as 9 slots per page). Extra pictures flow to additional pages. Click an image to select it; in any layout, drag it to move it, or drag any of its four lime corners to resize its slot. Select another picture to edit it independently. Resizing is free-form and can change the slot’s aspect ratio or any custom measurements. Changing a layout, adding, removing or duplicating pictures reflows and resets all positions and sizes; the app asks first. Crops and rotations are retained.

To resize just one picture, click it on the A4 page. The **Resize ONE selected picture** panel shows its name and frame dimensions. Drag a lime corner on the picture, or enter width and height in millimetres and click **Resize selected**. Drag inside the frame to move it. Numeric sizes must be at least 5 mm and fit on the A4 page at the current position; move the picture up or left if necessary. The separate **Grid size for all** control rearranges every picture, not just the selected one. The width/height describe the A4 frame, not necessarily the printed image content: Fit can leave white space, and Fill can crop the edges.

To crop one picture, select it on the sheet and choose **Crop selected picture**. Drag a lime corner on the picture to resize the crop rectangle; drag inside it to move the rectangle. Choose **Apply crop**, **Reset crop**, or **Cancel**. Crop is non-destructive and affects only that placed picture, not the original or other copies. Duplicating copies the crop initially; you can then edit either independently. Rotation keeps the selected area and turns it with the image. Saved projects and recovery copies retain individual crops; earlier projects without crop data open uncropped.

The sheet-wide **Fit whole image / Fill & crop**, brightness (25–200%) and black-and-white settings still apply to all photos on all pages, in addition to individual crops. Fit shows the whole selected crop; Fill may cut more off its edges to cover the slot. TIFF uses its first frame in the picture-sheet editor.

Choose **Save A4 picture PDF** to save all pages in one PDF. The app can then open your usual PDF viewer, where you can choose a printer and print all the pages together. The app does not directly control the printer. Select A4 and check the print preview; margins, paper handling and colour depend on the printer. Source images must remain available until export. Test with a small number of files before large batches.

The accompanying phone HTML is a visual, interactive preview; its browser print feature depends on the device and may not work inside the embedded viewer. Download the HTML and open it in the phone browser if needed. It is separate from the desktop app, and edits in one do not transfer to the other.

## CV designs and longer CVs

From **Career documents**, choose **View 13 CV designs** to open the local gallery. It shows real sample PDFs, with or without an optional photo, plus a three-page sidebar example. The gallery uses fictional sample data; its images are embedded and it does not upload anything. Each of the 13 designs can span multiple pages, with page numbers and a continued-page marker. The optional photo appears on the first page.

Use the multi-line Skills, Experience and Education fields and/or edit the draft to add more entries. Leave section headings in uppercase (for example `PROJECTS` or `REFERENCES`) to give them a styled heading. PDF output flows naturally across pages rather than shrinking content to fit. Three designs use a left or right sidebar on the first page for contact details, an optional photo, and short skills; content flows on later pages. Long skills stay in the main flowing content. Compact Profile uses smaller type and spacing, but does not force every CV onto one page or discard text. Review exported documents: line breaks vary by content and built-in fonts may not support every character.

`cv-layout-samples.pdf` contains one example of each of the 13 designs, and `cv-multiple-pages-sample.pdf` is an actual multi-page example. The separate photo-editor HTML preview is an earlier illustrative view and does not represent the latest CV designs; use `cv-design-gallery.html` for the actual layouts.

## Use your CV in PDF merge

In **Career documents**, create and review a draft, then click **Add to PDF merge**. The app generates a temporary PDF copy using your current template, text and optional CV photo and adds it to the PDF merge list. Open **PDF tools**, add other PDF files, arrange their order and click **Merge & save**. This also works for other career document types. Editing the CV afterwards does not change a copy already in the merge list; add it again to use the latest version.

These generated merge copies are stored in a private session temporary directory. Normal app exit or **Next customer** attempts to remove them; unexpected termination can leave files behind, and deletion is not secure wiping. The merged PDF you choose to save is a separate file and is not deleted. Use appropriate operating-system account security and data-handling practices.

The gallery includes a merge-workflow simulation for viewing only; actual merging is performed by the desktop app.

## Custom photo size and rotation

In **A4 picture sheets**, click a photo to select it and use **Rotate selected** to turn it 90° clockwise. Repeat as needed. You can duplicate a selected photo. Enter your own width and height in millimetres and choose **Apply custom size**. The app lays out as many slots as fit on each A4 page, using 7 mm page margins and 3 mm gaps. Width must be 5–196 mm; height 5–283 mm. There are no preset ID or passport-photo sizes.

A custom size describes the **slot** at the time it is applied. Dragging a corner afterwards changes that slot’s dimensions. Choose **Fill & crop** to fill it; **Fit whole image** may leave white space. Save the A4 PDF and print with **actual size / 100% scale**, not “fit to page”. Printers can impose margins or scaling: measure a test print with a ruler. Official photo requirements vary and may also specify background, head position and other rules; the app does not validate compliance.

## Recording sales and viewing reports

1. In PIN-protected **Price settings**, enter your own services and prices. The list starts empty.
2. In **Receipts**, add services and quantities. **Save receipt PDF** saves a document but does **not** record a sale.
3. Choose **Record sale**, unlock staff access, and confirm. The current receipt becomes a single local sale record; use **New receipt** before adding a different sale. A reference prevents the same in-session receipt from being recorded twice.
4. Open PIN-protected **Sales reports**, choose a local date range and view sale details and gross totals grouped by currency. The screen displays the latest 500 records; CSV export includes the whole range. A selected sale can be saved as a sale-record PDF (not a copy of the original PDF and not proof of payment). Receipt creation time and sale-recording time can differ.

No payment method, payment status, tax, refunds or automatic transaction editing/deletion are provided. Records are not proof of payment or a compliant tax invoice. This is an early local prototype, not an accounting or point-of-sale system; manually reconcile totals and test thoroughly. Sales are saved in `sales.sqlite3` within the current computer user's local `CafeDesk` folder, **not on USB or shared between computers**. Renaming the business does not create a separate sales database; individual records retain the business name captured at the time. To back up, close the app and copy the folder to a secure location; CSV export provides a readable report but is not a full database restore. The staff PIN is app-level only, and the database is not encrypted against computer-level access. Protect backups and exports as well.

## Price list for staff and customers

Open **Price list** from the sidebar or the home screen. Anyone using the app can view and search the saved service names and prices without unlocking staff settings. The page is read-only. It starts empty; a staff member must set the business's own rates using **Staff: edit prices**, which opens the PIN-protected **Price settings** screen. Enter units in each service name where appropriate, such as “Black-and-white printing per page,” so prices are clear.

The page also provides **Create a receipt**, **Refresh** and **Save full list as PDF**. The PDF contains all services even if a search filter is active; open the saved PDF in a viewer to print it. The prices are stored per computer user and are not automatically synced between computers. Exports are snapshots and do not update if prices later change.

The phone preview begins with an empty list too. To try it, use **Staff: edit prices**, add a demonstration service and price, then return to **Price list**. Changes in the preview are temporary, and the PIN is not enforced there. The real desktop app requires staff access to edit prices, though its app-level PIN cannot protect local files against computer-level access.

## Save and reopen editable projects

Use **Save project** in Career documents or A4 picture sheets, or use **Projects & recovery**. Choose a location and save a `.cafeproject` file. It contains the current editable text or picture layout and copies of included photos. The file can be moved to another computer running this version of Café Desk. Open it using **Open project**; opening replaces the current work of that project type after confirmation. It does not automatically save your previous work.

Project files are **not encrypted**. They can contain sensitive customer details and photos; obtain permission, store them securely and delete them when appropriate. Limits: 20 MB per distinct image, 80 MB combined image data, up to 200 pictures, and up to one million draft characters. Original files and saved PDFs are separate. Opening a project extracts its included photos into the app's temporary session folder; the app attempts to remove these on normal exit or **Next customer**. Unexpected termination can leave temporary files.

## Optional recovery copies

Recovery is **off by default** and must be enabled for each session from **Projects & recovery** after the privacy warning. While enabled and the app is not busy, it saves local career and/or picture-sheet project copies approximately every 60 seconds. Recovery may be delayed if the app is busy, and the latest changes since the last copy can be lost. Files are retained after closing or a crash so work can be recovered; they are unencrypted and contain photos and text. The home page indicates when recovery copies exist. Staff use their PIN to choose **Recover career** or **Recover picture sheet**; opening still asks before replacing current work.

**Next customer** attempts to remove the recovery copies and turns recovery off, but does not delete user-saved projects, PDFs or source images. Staff can separately choose **Delete recovery copies**. These actions are ordinary file deletion, not secure erasure; other copies and backups may remain. The recovery folder is inside the current operating-system user's `CafeDesk/recovery` folder and is not shared across computers.

## Beginner help and shared-computer privacy

Open **Help & privacy** for a quick guide. The **? Help** button gives instructions for the current screen. Staff settings lock after roughly five minutes of inactivity; the current document is not erased by this lock. **Next customer** clears the current workspace, temporary merge files and recovery copies and locks staff, but intentionally leaves saved files and originals alone. Staff can use **Delete a selected customer file** with two confirmations; it permanently deletes only the chosen regular document/image, does not use the Recycle Bin, does not guarantee secure erasure and does not remove copies. The control will not delete files inside Café Desk's own settings folder.

There are still no separate customer accounts, encryption, secure deletion, kiosk isolation or protection from someone with operating-system access. Use separate OS accounts and appropriate procedures. Staff should click **Next customer** between users and explicitly handle saved files according to customer consent and the café's policies.

The desktop-style phone preview includes sample illustrations for trying individual crops and resizing. Its browser editing is a simulation; it is not the desktop program and does not create `.cafeproject` files.

## Preview pages before extracting a PDF

Open **PDF tools**, choose a PDF under **Split or extract pages**, and select **Preview & select pages**. The selector renders up to six pages at a time locally. Click page thumbnails or **Select** to choose or unchoose pages, and use **View larger** to inspect a page. Previous/Next page groups keep your selection. **Use selected pages** fills the page-number box; then choose **Extract pages** and a new filename. Selected pages are saved in their original document order. For convenience, you can still type ranges manually and split every page.

Preview uses PDFium via `pypdfium2`, installed with the Python requirements and included by the packaging scripts. It requires a supported build on the target platform. Preview renders locally and does not send the PDF online. The selector supports up to 2,000 pages (and 150 MB per PDF) and renders six thumbnails at a time; complex or malformed PDFs may not preview, and document features such as interactive forms may render differently. Review the extracted file. PDFs requiring a password are unsupported. If the file changes while previewing, select it again before extracting. Preview images stay in memory; saved extracted PDFs are separate files and are not deleted by **Next customer**.

`page-selection-preview.html` is a phone-friendly interactive simulation with sample pages, not a PDF extractor and not a way to load customer PDFs. The actual feature requires the desktop application. All dependency pins in `requirements.txt`, `pyproject.toml`, and `requirements-lock.txt` enforce `Pillow>=13.0.0` (`requires-python >= 3.11`) and current security updates.

The phone scanner preview is a simulation only. It cannot discover or scan physical devices.

## Receive a file using Bluetooth (Windows 10/11)

1. Turn Bluetooth on in Windows and on the phone. In **Bluetooth files** choose **Open Windows Bluetooth settings** and pair the phone, confirming the pairing on both devices.
2. Choose **Receive via Bluetooth**. This opens the separate Windows Bluetooth File Transfer window. In that window click **Receive files**; wait until Windows says it is ready.
3. Send the file from the paired phone using its Bluetooth sharing option. In the Windows transfer window, choose the destination folder and **Finish**.
4. Return to Café Desk and choose **Add received PDF** or **Add received images**, or browse for the file in the relevant tool. Café Desk does not automatically accept or open transfers.

A compatible Bluetooth adapter, working Windows driver and a phone that supports Bluetooth file sharing are needed. Compatible Android devices can usually share files this way; iPhones generally cannot use ordinary Bluetooth file transfer to a Windows PC. Use an appropriate alternative for the phone and file type. The Bluetooth wizard and pairing are controlled by Windows, not by Café Desk. They have not been tested on a real Windows 10/11 device in this environment. On macOS/Linux, these Windows shortcuts are not available; use the operating system's sharing tools.

Only accept expected files from trusted senders and use operating-system malware protection. Received files are saved by Windows, not in the app's temporary session. **Next customer** clears app file lists, but does not delete received files or remove Bluetooth pairings. Handle saved files and pairings according to your privacy policy. The phone preview is simulated and does not communicate over Bluetooth.

## Multi-PC Shops (2+ Computers), Supabase Cloud & Remote Over-the-Internet Updates

Café Desk works **100% offline-first** and also supports shops with **2, 4, or 10+ computers** plus optional **Supabase** cloud features so you never have to visit customer shops physically to deliver updates:

1. **Batch Multi-PC Shop Licensing (`admin_keygen.py` — private owner tool, gitignored):**
   - Issue hardware-locked keys (`Seat 1 of N`, `Seat 2 of N`, …) for all computers in one shop in a single command:
     ```bash
     python admin_keygen.py --machine-ids "CD-1111-2222-3333=Counter PC,CD-4444-5555-6666=Desk 2,CD-7777-8888-9999=Desk 3" \
       --customer "Sunrise Cyber Café" --shop-id SHOP-SUNRISE --pdf-unlock-seats seat1
     ```
   - Saves per-seat `.cafelicense` files + a printable `<SHOP_ID>-ALL-SEATS.txt` handout inside gitignored `owner-keys/issued-files/<SHOP_ID>/`, and groups seats inside `owner-keys/issued-licenses-dashboard.html`.
2. **Multi-PC Price List & Combined Sales Sync (Offline LAN/USB or Supabase Cloud):**
   - In **Business settings**, name each computer (`Counter PC 1`, `Desk 2`, …) and use **Export / Import Shop Pack (`.cafeshop.json`)** or **Push / Pull Prices via Cloud** so all PCs share the exact same price list.
   - In **Sales reports**, every sale records its **Computer / Station**, displays per-computer and combined shop totals, and merges sales across PCs with zero duplicates via **Sync Shared LAN Folder**, **Import Other PC File**, or **Sync via Supabase** (customer documents are **never** uploaded).
3. **Supabase Setup & Remote Over-the-Internet App Updater (`supabase_schema.sql` + `cloud_sync.py`):**
   - Paste `supabase_schema.sql` into your Supabase SQL Editor and click **Run**.
   - On your private computer, save your Supabase URL and `service_role` key inside gitignored `owner-keys/supabase-owner.json`:
     ```bash
     python admin_keygen.py --setup-supabase https://YOUR-PROJECT.supabase.co YOUR_SERVICE_ROLE_KEY
     python admin_keygen.py --sync-supabase
     python admin_keygen.py --publish-release 0.8.1 https://YOUR-RELEASE-URL/CafeDesk-Setup.exe dist/CafeDesk-Setup.exe --release-notes "Bug fixes & faster OCR"
     ```
    - Customer PCs only ever store the **public** Supabase URL + `anon` key and verify an exact **SHA-256 checksum** over HTTPS before running any remote installer (preserving `%APPDATA%\CafeDesk\` licenses, PIN, prices, and sales DB).
4. **Owner Cloud Web Dashboard (`owner-dashboard.html` — Mobile & Laptop Browser):**
   - Open `owner-dashboard.html` on your phone or laptop (or host on GitHub Pages at `https://njwacky.github.io/internetcafe/owner-dashboard.html`).
   - Protected by Supabase Auth (`disable_signup = true`, locked strictly to your owner email) + the `owner-admin` Supabase Edge Function, which signs genuine Ed25519 `CDLIC1` / `CDADD1` keys inside Supabase's secret vault so your private signing key never touches the browser or GitHub.
   - Issue single or Multi-PC shop keys in 1 tap, send keys straight to WhatsApp, approve pending customer requests, remotely revoke keys, and upload + SHA-256 publish new `CafeDesk-Setup.exe` updates from anywhere.


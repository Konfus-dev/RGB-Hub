// SPDX-License-Identifier: GPL-3.0-or-later
//
// The RGB Hub settings page. It mirrors the GNOME settings window
// (frontends/gnome-shell/rgbhub@konfus-dev.github.io/prefs.js): the same
// tabs, rows and wording. Like that window, it only reads and writes
// settings; the hub does the work and reports back through "status".
//
// Tabs: Auto (every device's auto switch and the Default look), then one
// tab per device. A device's tab is there even while it is unplugged.
//
// Talks to service/rgbhub/core/web.py: GET /api/state every 2 seconds,
// POST /api/set for each change.

'use strict';

const MAX_RECENT_IMAGES = 8; // how many are remembered
const RECENT_IMAGES_SHOWN = 6; // how many thumbnails fit on the page
const DEFAULT_COLOUR = '#FFFFFF';
const DEFAULT_MODE = 'Static';
const FIT_VALUES = ['fill', 'fit'];
const FIT_LABELS = ['Fill the screen (crop edges)', 'Show the whole image (black bars)'];
const POLL_MS = 2000;
const SEND_DELAY_MS = 120; // a slider or colour being dragged is sent at most this often

// Used only while the hub has not reported its devices (not running yet); once it
// runs, the devices come from its status report (see hubDevices).
const DEVICES = [
    {id: 'OMEN-PC', label: 'OMEN-PC', icon: 'computer', lcd: true},
    {id: 'IQUNIX-KEYBOARD', label: 'IQUNIX-KEYBOARD', icon: 'input-keyboard', lcd: false},
];

const token = new URLSearchParams(location.search).get('t') ?? '';
let state = {settings: {}, status: {}};
let writes = 0; // changes sent so far; a poll begun before the latest one is out of date
let updaters = []; // functions that bring the controls in step with `state`
let builtFor = null; // what the pages were last built from
let currentTab = 'auto';

// --- talking to the hub -----------------------------------------------------------

function api(path, options = {}) {
    const headers = {'X-RGBHub-Token': token, ...(options.headers ?? {})};
    return fetch(path, {...options, headers});
}

async function poll() {
    const seen = writes;
    try {
        const response = await api('/api/state');
        if (!response.ok)
            throw new Error(response.statusText);
        const fresh = await response.json();
        document.getElementById('offline').hidden = true;
        if (seen !== writes)
            return; // a change was sent meanwhile; the next poll will include it
        state = fresh;
        render();
    } catch {
        document.getElementById('offline').hidden = false;
    }
}

/** Send one change to the hub, and show it on the page at once. */
function send(path, body) {
    writes += 1;
    api(path, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
    }).catch(() => (document.getElementById('offline').hidden = false));
    update();
}

/** Change one setting. */
function setSetting(key, value) {
    if (JSON.stringify(state.settings[key]) === JSON.stringify(value))
        return;
    state.settings[key] = value;
    send('/api/set', {key, value});
}

/**
 * Change some entries of a table setting (zone-colours, device-modes, ...).
 * Only these entries are sent; the hub merges them into the table as it is
 * then, so the page being a moment behind never undoes another change.
 */
function setEntries(key, entries) {
    const table = state.settings[key] ?? {};
    if (Object.entries(entries).every(([name, value]) => table[name] === value))
        return; // nothing new: avoid a pointless write
    state.settings[key] = {...table, ...entries};
    send('/api/set-entries', {key, entries});
}

function getEntry(key, name, fallback) {
    return (state.settings[key] ?? {})[name] ?? fallback;
}

/** `fn`, called at most once every SEND_DELAY_MS with the latest arguments. */
function throttled(fn) {
    let timer = null;
    let latest = null;
    return (...args) => {
        latest = args;
        if (timer === null) {
            timer = setTimeout(() => {
                timer = null;
                fn(...latest);
            }, SEND_DELAY_MS);
        }
    };
}

// --- small helpers ------------------------------------------------------------------

/** `list` with `path` moved to the front, no duplicates, at most 8 entries. */
function pushRecent(list, path) {
    return [path, ...list.filter(p => p !== path)].slice(0, MAX_RECENT_IMAGES);
}

/** The devices the hub reported (id, label, icon, has-lcd), or the fallback list. */
function hubDevices() {
    const reported = state.status.devices ?? [];
    if (reported.length === 0)
        return DEVICES;
    return reported.map(device => ({
        id: device.id,
        label: device.label ?? device.id,
        icon: device.icon ?? 'computer',
        lcd: Boolean(device['has-lcd']),
    }));
}

/** One device's part of the status report, or null. */
function deviceStatus(id) {
    return (state.status.devices ?? []).find(device => device.id === id) ?? null;
}

function basename(path) {
    return path.split(/[\\/]/).pop();
}

function normalHex(hex) {
    return /^#[0-9a-fA-F]{6}$/.test(hex ?? '') ? hex.toUpperCase() : DEFAULT_COLOUR;
}

/** Bring the controls in step with the settings. */
function update() {
    updaters.forEach(fn => fn());
}

/** Run `fn` at every update, except while the person is using `control`. */
function follow(control, fn) {
    updaters.push(() => {
        if (document.activeElement !== control || control.type === 'checkbox')
            fn();
    });
}

function element(tag, props = {}, children = []) {
    const node = document.createElement(tag);
    for (const [name, value] of Object.entries(props)) {
        if (name === 'className')
            node.className = value;
        else if (name in node)
            node[name] = value;
        else
            node.setAttribute(name, value);
    }
    for (const child of children)
        node.append(child);
    return node;
}

// --- pictures --------------------------------------------------------------------

const pictureUrls = new Map(); // path -> a promise of a blob: URL

/** A picture the settings name, as something an <img> can show. */
function pictureUrl(path) {
    if (!pictureUrls.has(path)) {
        pictureUrls.set(path, api(`/api/image?path=${encodeURIComponent(path)}`)
            .then(response => (response.ok ? response.blob() : null))
            .then(blob => (blob ? URL.createObjectURL(blob) : ''))
            .catch(() => ''));
    }
    return pictureUrls.get(path);
}

function showPicture(img, path) {
    img.dataset.path = path;
    if (!path) {
        img.hidden = true;
        img.removeAttribute('src');
        return;
    }
    pictureUrl(path).then(url => {
        if (img.dataset.path !== path)
            return; // another picture was chosen meanwhile
        img.hidden = !url;
        if (url)
            img.src = url;
    });
}

async function uploadPicture(file) {
    const response = await api('/api/picture', {
        method: 'POST',
        body: file,
    });
    if (!response.ok)
        throw new Error((await response.json().catch(() => ({}))).error ?? response.statusText);
    return (await response.json()).path;
}

// --- rows -------------------------------------------------------------------------

function group(title, description) {
    const rows = element('div', {className: 'rows'});
    const section = element('section', {className: 'group'});
    if (title)
        section.append(element('h2', {textContent: title}));
    if (description)
        section.append(element('p', {className: 'description', textContent: description}));
    section.append(rows);
    section.add = row => rows.append(row);
    return section;
}

function row(title, subtitle, ...controls) {
    const subtitleNode = element('div', {className: 'subtitle', textContent: subtitle ?? ''});
    subtitleNode.hidden = !subtitle;
    const text = element('div', {className: 'text'}, [
        element('div', {className: 'title', textContent: title}),
        subtitleNode,
    ]);
    const node = element('div', {className: 'row'}, [
        text, element('div', {className: 'control'}, controls),
    ]);
    node.setSubtitle = value => {
        subtitleNode.textContent = value ?? '';
        subtitleNode.hidden = !value;
    };
    return node;
}

function switchControl(label) {
    return element('input', {type: 'checkbox', className: 'switch', role: 'switch',
        'aria-label': label});
}

/** A switch for a plain on/off setting (lcd-enabled, auto-lights, auto-lcd). */
function switchRow(key, title, subtitle) {
    const input = switchControl(title);
    input.addEventListener('change', () => setSetting(key, input.checked));
    follow(input, () => (input.checked = Boolean(state.settings[key])));
    return row(title, subtitle, input);
}

/**
 * A switch for one entry of a "name -> on/off" setting (device-enabled,
 * auto-devices). `fallback` is what a missing entry means.
 */
function deviceSwitchRow(key, name, fallback, title) {
    const input = switchControl(title);
    input.addEventListener('change', () => setEntries(key, {[name]: input.checked}));
    follow(input, () => (input.checked = getEntry(key, name, fallback)));
    const node = row(title, null, input);
    node.input = input;
    return node;
}

/** A 0-100 slider; `read` gives the value to show, `write` saves one. */
function slider(title, subtitle, read, write) {
    const input = element('input', {type: 'range', min: 0, max: 100, step: 1,
        'aria-label': title});
    const shown = element('span', {className: 'value'});
    const send = throttled(write);
    input.addEventListener('input', () => {
        shown.textContent = input.value;
        send(Number(input.value));
    });
    input.addEventListener('change', () => input.blur()); // let the page follow it again
    follow(input, () => {
        input.value = read();
        shown.textContent = input.value;
    });
    return row(title, subtitle, input, shown);
}

/** A slider tied to an integer setting (lcd-brightness). */
function sliderRow(key, title) {
    return slider(title, null, () => state.settings[key] ?? 0, value => setSetting(key, value));
}

/** A slider for one device in a "name -> number" setting (speed, dimmest, brightest, ...). */
function deviceSliderRow(key, name, title, subtitle, fallback) {
    return slider(title, subtitle, () => getEntry(key, name, fallback),
        value => setEntries(key, {[name]: value}));
}

function colourInput(title) {
    const input = element('input', {type: 'color', 'aria-label': title, title});
    input.addEventListener('change', () => input.blur());
    return input;
}

/** A group that shows one warning line, hidden while there is nothing to say. */
function warningGroup() {
    const section = group();
    const line = row('');
    line.classList.add('warning');
    section.add(line);
    section.hidden = true;
    section.show = message => {
        line.querySelector('.title').textContent = `⚠ ${message ?? ''}`;
        section.hidden = !message;
    };
    return section;
}

// --- Cooler LCD (on the OMEN-PC tab) ------------------------------------------------

function lcdGroup() {
    const section = group('Cooler LCD', 'The round screen on the CPU cooler.');
    section.add(switchRow('lcd-enabled', 'Screen on', 'Off shows a black screen'));

    // Preview of the chosen image.
    const preview = element('img', {alt: ''});
    preview.hidden = true;
    const previewRow = element('div', {className: 'row center'}, [
        element('div', {className: 'lcd-preview'}, [preview]),
    ]);
    section.add(previewRow);

    // Image chooser.
    const fileInput = element('input', {type: 'file',
        accept: 'image/png,image/jpeg,image/gif,image/bmp,image/webp'});
    fileInput.hidden = true;
    const chooseButton = element('button', {className: 'action', type: 'button',
        textContent: 'Choose image…'});
    const clearButton = element('button', {className: 'action flat', type: 'button',
        textContent: '✕', title: 'Go back to the OMEN logo',
        'aria-label': 'Go back to the OMEN logo'});
    const imageRow = row('Image', null, clearButton, chooseButton, fileInput);
    section.add(imageRow);

    chooseButton.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', async () => {
        const file = fileInput.files[0];
        fileInput.value = '';
        if (!file)
            return; // the person pressed Cancel
        chooseButton.disabled = true;
        try {
            const path = await uploadPicture(file);
            setSetting('lcd-recent-images',
                pushRecent(state.settings['lcd-recent-images'] ?? [], path));
            setSetting('lcd-image', path);
        } catch (error) {
            imageRow.setSubtitle(`Could not use ${file.name}: ${error.message}`);
        } finally {
            chooseButton.disabled = false;
        }
    });
    clearButton.addEventListener('click', () => setSetting('lcd-image', ''));

    // Recently used images: click one to show it again.
    const recentBox = element('div', {className: 'recent'});
    const recentRow = element('div', {className: 'row center'}, [recentBox]);
    section.add(recentRow);
    let recentShown = null;
    updaters.push(() => {
        const recent = (state.settings['lcd-recent-images'] ?? []).slice(0, RECENT_IMAGES_SHOWN);
        recentRow.hidden = recent.length <= 1;
        const signature = JSON.stringify(recent);
        if (signature === recentShown)
            return;
        recentShown = signature;
        recentBox.replaceChildren(...recent.map(path => {
            const img = element('img', {alt: basename(path)});
            showPicture(img, path);
            const button = element('button', {type: 'button', title: basename(path)}, [img]);
            button.addEventListener('click', () => setSetting('lcd-image', path));
            return button;
        }));
    });

    // Fill or fit.
    const fit = element('select', {'aria-label': 'Fit'},
        FIT_LABELS.map((label, i) => element('option', {value: FIT_VALUES[i], textContent: label})));
    fit.addEventListener('change', () => {
        setSetting('lcd-fit', fit.value);
        fit.blur(); // let the page follow it again
    });
    section.add(row('Fit', null, fit));

    section.add(sliderRow('lcd-brightness', 'Brightness'));

    // Keep the preview and labels in step with the settings.
    updaters.push(() => {
        const path = state.settings['lcd-image'] ?? '';
        const mode = state.settings['lcd-fit'] ?? 'fill';
        if (preview.dataset.path !== path)
            showPicture(preview, path);
        preview.style.objectFit = mode === 'fit' ? 'contain' : 'cover';
        if (!chooseButton.disabled)
            imageRow.setSubtitle(path ? basename(path) : 'None (OMEN logo)');
        clearButton.disabled = !path;
    });
    follow(fit, () => (fit.value = FIT_VALUES.includes(state.settings['lcd-fit'])
        ? state.settings['lcd-fit'] : 'fill'));
    return section;
}

// --- Device pages ------------------------------------------------------------------

/**
 * One group per lighting device: an effect chooser, an "all" colour and a
 * colour per zone. `light` comes from the hub's status report:
 *   name, zones            real names, used as keys when saving
 *   label, zone_labels     what to show
 *   modes, default_mode    the effects on offer
 *   colourless_modes       effects that ignore colours (their rows are hidden)
 *   speed_modes            effects that move (they get a speed slider)
 *   range_modes            effects that pulse (they get dimmest/brightest sliders)
 */
function lightGroup(light) {
    const section = group(light.label ?? light.name);
    const zones = light.zones ?? [];
    const modes = light.modes ?? [];
    const zoneLabels = light.zone_labels ?? zones;
    const colourless = light.colourless_modes ?? [];
    const speedModes = light.speed_modes ?? [];
    const rangeModes = light.range_modes ?? [];
    const zoneKey = zone => `${light.name}/${zone}`;
    const zoneColour = zone => normalHex(getEntry('zone-colours', zoneKey(zone), DEFAULT_COLOUR));
    const savedMode = () => getEntry('device-modes', light.name,
        light.default_mode || DEFAULT_MODE);

    // Effect.
    const effect = element('select', {'aria-label': 'Effect'},
        modes.map(mode => element('option', {value: mode, textContent: mode})));
    effect.addEventListener('change', () => {
        setEntries('device-modes', {[light.name]: effect.value});
        effect.blur(); // let the page follow it again
    });
    section.add(row('Effect', null, effect));

    // Extra sliders that only appear for effects that use them.
    const speedRow = deviceSliderRow('device-speeds', light.name,
        'Speed', 'How fast the effect moves', 50);
    const brightestRow = deviceSliderRow('device-max-brightness', light.name,
        'Brightest', 'The top of each pulse', 100);
    const dimmestRow = deviceSliderRow('device-min-brightness', light.name,
        'Dimmest', 'The bottom of each pulse', 0);
    [speedRow, brightestRow, dimmestRow].forEach(r => section.add(r));

    // One colour for everything, for the common case.
    const colourRows = [];
    if (zones.length > 1) {
        const all = colourInput('All');
        const send = throttled(hex => setEntries('zone-colours',
            Object.fromEntries(zones.map(zone => [zoneKey(zone), hex]))));
        all.addEventListener('input', () => send(all.value.toUpperCase()));
        follow(all, () => (all.value = zoneColour(zones[0]).toLowerCase()));
        const allRow = row('All', 'Set every colour below at once', all);
        section.add(allRow);
        colourRows.push(allRow);
    }

    // A colour per zone.
    zones.forEach((zone, index) => {
        const title = zoneLabels[index] ?? zone;
        const input = colourInput(title);
        const send = throttled(hex => setEntries('zone-colours', {[zoneKey(zone)]: hex}));
        input.addEventListener('input', () => send(input.value.toUpperCase()));
        follow(input, () => (input.value = zoneColour(zone).toLowerCase()));
        const zoneRow = row(title, null, input);
        section.add(zoneRow);
        colourRows.push(zoneRow);
    });

    // Show only the rows the chosen effect actually uses.
    follow(effect, () => {
        if (modes.includes(savedMode()))
            effect.value = savedMode();
    });
    updaters.push(() => {
        const mode = modes.includes(savedMode()) ? savedMode() : modes[0];
        speedRow.hidden = !speedModes.includes(mode);
        brightestRow.hidden = dimmestRow.hidden = !rangeModes.includes(mode);
        colourRows.forEach(r => (r.hidden = colourless.includes(mode)));
    });
    return section;
}

/** What is wrong with a device right now, or null. */
function deviceProblem(device) {
    const report = deviceStatus(device.id);
    if (!report)
        return 'The RGB Hub service is not running.';
    if (report.error)
        return report.error;
    if (!report.connected)
        return `${device.label} is not connected.`;
    if (device.lcd && !report.lcd)
        return 'Cooler LCD not found.';
    if (device.lcd && !report.case)
        return 'The case lights cannot be reached (is OpenRGB running?).';
    return null;
}

/** One device's tab: its lights, and for the OMEN PC its cooler LCD. */
function devicePage(device) {
    const page = element('div');
    const warning = warningGroup();
    page.append(warning);
    updaters.push(() => warning.show(deviceProblem(device)));

    if (device.lcd)
        page.append(lcdGroup());

    const lights = group('Lights');
    lights.add(deviceSwitchRow('device-enabled', device.id, true, 'Lights on'));
    lights.add(deviceSliderRow('device-brightness', device.id, 'Brightness', null, 100));
    page.append(lights);

    for (const light of deviceStatus(device.id)?.lights ?? [])
        page.append(lightGroup(light));
    return page;
}

// --- Auto page -----------------------------------------------------------------------

function autoPage(devices) {
    const page = element('div');
    const section = group('Auto mode',
        'Match your devices to the app you are using. With nothing in focus, ' +
        'or an app without an icon, they match the desktop: its wallpaper and colour. ' +
        'Your normal settings show while this window has focus.');
    page.append(section);

    // The master switch is on while every device follows; flipping it sets them all.
    const everyOn = () => devices.every(device => getEntry('auto-devices', device.id, false));
    const master = switchControl('Auto mode');
    master.addEventListener('change', () => setEntries('auto-devices',
        Object.fromEntries(devices.map(device => [device.id, master.checked]))));
    follow(master, () => (master.checked = everyOn()));
    section.add(row('Auto mode', 'Every device', master));

    // What auto mode last picked up, as reported by the hub.
    const swatch = element('span', {className: 'swatch'});
    const nowRow = row('Now following', null, swatch);
    section.add(nowRow);
    updaters.push(() => {
        const anyOn = Object.values(state.settings['auto-devices'] ?? {}).some(v => v);
        const auto = state.status.auto ?? {};
        nowRow.hidden = !anyOn;
        nowRow.setSubtitle(auto.app
            ? auto.app.replace(/\.desktop$/, '')
            : auto.colour ? 'Nothing in focus (desktop look)' : 'Your normal settings');
        swatch.hidden = !auto.colour;
        if (auto.colour)
            swatch.style.background = normalHex(auto.colour);
    });

    const perDevice = group('Per device');
    page.append(perDevice);
    for (const device of devices) {
        const deviceRow = deviceSwitchRow('auto-devices', device.id, false, device.label);
        perDevice.add(deviceRow);
        if (!device.lcd)
            continue;
        const subRows = [
            switchRow('auto-lights', 'Lights follow the app',
                'Uses the main colour of the app’s icon'),
            switchRow('auto-lcd', 'LCD shows the app',
                'Puts the app’s icon on the cooler screen'),
        ];
        for (const sub of subRows) {
            sub.classList.add('indent');
            perDevice.add(sub);
        }
        updaters.push(() => {
            const on = getEntry('auto-devices', device.id, false);
            for (const sub of subRows) {
                sub.classList.toggle('disabled', !on);
                sub.querySelector('input').disabled = !on;
            }
        });
    }

    page.append(defaultGroup());
    return page;
}

/** Ask the hub to save or load the Default; it clears the request once done. */
function askHub(action) {
    state.settings['default-action'] = ''; // ask again even if the last reply is not in yet
    setSetting('default-action', action);
}

/** The Default look: what every device shows once the hub stops. */
function defaultGroup() {
    const section = group('Default look',
        'Shown on every device when RGB Hub stops (shutting down, logging out, ' +
        'or the service being stopped), so whatever starts next shows it too. ' +
        'The keyboard keeps it by itself; the cooler screen keeps the last picture sent. With no Default saved: purple lights and Spike on the screen.');
    const saveButton = element('button', {className: 'action', type: 'button',
        textContent: 'Save'});
    saveButton.addEventListener('click', () => askHub('save'));
    const saveRow = row('Save current look as Default',
        'Each device’s own settings, as on its tab', saveButton);
    section.add(saveRow);

    const loadButton = element('button', {className: 'action', type: 'button',
        textContent: 'Load'});
    loadButton.addEventListener('click', () => askHub('load'));
    section.add(row('Go back to the Default',
        'Makes the Default each device’s own settings again', loadButton));

    updaters.push(() => {
        const running = state.status.devices !== undefined;
        saveButton.disabled = !running;
        loadButton.disabled = !running || !state.status['has-default'];
        saveRow.setSubtitle(running
            ? 'Each device’s own settings, as on its tab'
            : 'Needs the RGB Hub service running');
    });
    return section;
}

// --- tabs ------------------------------------------------------------------------------

/** Build every tab again, but only when the devices or their lights really changed. */
function render() {
    const devices = hubDevices();
    const signature = JSON.stringify([devices, devices.map(d => deviceStatus(d.id)?.lights)]);
    if (signature !== builtFor) {
        builtFor = signature;
        updaters = [];
        const tabs = [{id: 'auto', label: 'Auto', page: autoPage(devices)}, ...devices.map(
            device => ({id: device.id, label: device.label, page: devicePage(device)}))];
        if (!tabs.some(tab => tab.id === currentTab))
            currentTab = 'auto';
        const nav = document.getElementById('tabs');
        const pages = document.getElementById('pages');
        nav.replaceChildren();
        pages.replaceChildren();
        for (const tab of tabs) {
            const button = element('button', {type: 'button', role: 'tab',
                textContent: tab.label});
            const select = () => {
                currentTab = tab.id;
                for (const other of tabs) {
                    other.button.setAttribute('aria-selected', String(other === tab));
                    other.page.hidden = other !== tab;
                }
            };
            button.addEventListener('click', select);
            tab.button = button;
            nav.append(button);
            pages.append(tab.page);
        }
        tabs.find(tab => tab.id === currentTab).button.click();
    }
    update();
}

render(); // the fallback tabs at once, before the first reply
poll();
setInterval(poll, POLL_MS);

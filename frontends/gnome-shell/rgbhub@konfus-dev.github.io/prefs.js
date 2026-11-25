// SPDX-License-Identifier: GPL-3.0-or-later
//
// The RGB Hub settings window (opened from "Settings…" in the system
// menu, or from the Extensions app).
//
// Like extension.js, this only reads and writes settings; the background
// hub does the work and reports back through the "status" setting.
//
// Tabs: Auto (every device's auto switch and the Default look), then one
// tab per device. A device's tab is there even while it is unplugged.

import Adw from 'gi://Adw';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Gdk from 'gi://Gdk';
import Gio from 'gi://Gio';
import Gtk from 'gi://Gtk';

import {ExtensionPreferences} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

const MAX_RECENT_IMAGES = 8; // how many are remembered
const RECENT_IMAGES_SHOWN = 6; // how many thumbnails fit in the window
const THUMBNAIL_SIZE = 64;
const DEFAULT_COLOUR = '#FFFFFF';
const DEFAULT_MODE = 'Static';
const FIT_VALUES = ['fill', 'fit'];
const FIT_LABELS = ['Fill the screen (crop edges)', 'Show the whole image (black bars)'];

// Used only while the hub has not reported its devices (not running yet); once it
// runs, the devices come from its status report (see hubDevices).
const DEVICES = [
    {id: 'OMEN-PC', label: 'OMEN-PC', icon: 'computer-symbolic', lcd: true},
    {id: 'IQUNIX-KEYBOARD', label: 'IQUNIX-KEYBOARD', icon: 'input-keyboard-symbolic', lcd: false},
];

// --- small helpers ------------------------------------------------------------

/** `list` with `path` moved to the front, no duplicates, at most 8 entries. */
export function pushRecent(list, path) {
    return [path, ...list.filter(p => p !== path)].slice(0, MAX_RECENT_IMAGES);
}

/** What the hub last reported, or {} if it is not running. */
function readStatus(settings) {
    try {
        return JSON.parse(settings.get_string('status'));
    } catch {
        return {};
    }
}

/** The devices the hub reported (id, label, icon, has-lcd), or the fallback list. */
function hubDevices(settings) {
    const reported = readStatus(settings).devices ?? [];
    if (reported.length === 0)
        return DEVICES;
    return reported.map(device => ({
        id: device.id,
        label: device.label ?? device.id,
        icon: device.icon ?? 'computer-symbolic',
        lcd: Boolean(device['has-lcd']),
    }));
}

/** One device's part of the status report, or null. */
function deviceStatus(status, id) {
    return (status.devices ?? []).find(device => device.id === id) ?? null;
}

/** Call `refresh` whenever one of `keys` changes; returns a function that stops it. */
function watch(settings, keys, refresh) {
    const ids = keys.map(key => settings.connect(`changed::${key}`, refresh));
    return () => ids.forEach(id => settings.disconnect(id));
}

/** Run every function in `list` once, and empty it. */
function runAll(list) {
    list.splice(0).forEach(stop => stop());
}

/** Keep a function in step with some settings until the window closes. */
function follow(settings, window, keys, refresh) {
    const stop = watch(settings, keys, refresh);
    window.connect('close-request', () => {
        stop();
        return false;
    });
    refresh();
}

function rgbaToHex(rgba) {
    const part = v => Math.round(v * 255).toString(16).padStart(2, '0');
    return `#${part(rgba.red)}${part(rgba.green)}${part(rgba.blue)}`.toUpperCase();
}

function hexToRgba(hex) {
    const rgba = new Gdk.RGBA();
    if (!rgba.parse(hex))
        rgba.parse(DEFAULT_COLOUR);
    return rgba;
}

/** Read one entry, or write several, of a "dictionary of strings" setting. */
function getEntry(settings, key, name, fallback) {
    return settings.get_value(key).deepUnpack()[name] ?? fallback;
}

function setEntries(settings, key, entries) {
    const all = settings.get_value(key).deepUnpack();
    if (Object.entries(entries).every(([name, value]) => all[name] === value))
        return; // nothing new: avoid a pointless write
    settings.set_value(key, new GLib.Variant('a{ss}', {...all, ...entries}));
}

/**
 * A switch for one entry of a "name -> on/off" setting (device-enabled,
 * auto-devices). `fallback` is what a missing entry means.
 */
function deviceSwitchRow(settings, key, name, fallback, props) {
    const row = new Adw.SwitchRow(props);
    const read = () => settings.get_value(key).deepUnpack()[name] ?? fallback;
    row.active = read();
    row.connect('notify::active', () => {
        if (row.active === read())
            return;
        const all = settings.get_value(key).deepUnpack();
        settings.set_value(key, new GLib.Variant('a{sb}', {...all, [name]: row.active}));
    });
    const id = settings.connect(`changed::${key}`, () => {
        if (row.active !== read())
            row.active = read();
    });
    row.connect('destroy', () => settings.disconnect(id));
    return row;
}

/** A row with a 0-100 slider tied to an integer setting. */
function sliderRow(settings, key, title) {
    const scale = new Gtk.Scale({
        adjustment: new Gtk.Adjustment({lower: 0, upper: 100, step_increment: 1, page_increment: 10}),
        draw_value: true,
        digits: 0,
        value_pos: Gtk.PositionType.RIGHT,
        hexpand: true,
        width_request: 220,
        valign: Gtk.Align.CENTER,
    });
    settings.bind(key, scale.adjustment, 'value', Gio.SettingsBindFlags.DEFAULT);
    const row = new Adw.ActionRow({title});
    row.add_suffix(scale);
    return row;
}

/**
 * A row with a 0-100 slider for one device, stored in a "device name ->
 * number" setting (speed, dimmest, brightest). It follows the setting
 * (after "Load" of the Default, say) until the function it adds to
 * `cleanups` is called.
 */
function deviceSliderRow(settings, key, deviceName, title, subtitle, fallback, cleanups) {
    const read = () => settings.get_value(key).deepUnpack()[deviceName] ?? fallback;
    const scale = new Gtk.Scale({
        adjustment: new Gtk.Adjustment({
            lower: 0, upper: 100, step_increment: 1, page_increment: 10, value: read(),
        }),
        draw_value: true,
        digits: 0,
        value_pos: Gtk.PositionType.RIGHT,
        hexpand: true,
        width_request: 220,
        valign: Gtk.Align.CENTER,
    });
    let syncing = false; // true while the slider is being moved to match the setting
    scale.connect('value-changed', () => {
        if (syncing)
            return;
        const all = settings.get_value(key).deepUnpack();
        const value = Math.round(scale.get_value());
        if (all[deviceName] === value)
            return;
        settings.set_value(key, new GLib.Variant('a{si}', {...all, [deviceName]: value}));
    });
    cleanups.push(watch(settings, [key], () => {
        if (Math.round(scale.get_value()) === read())
            return;
        syncing = true;
        scale.set_value(read());
        syncing = false;
    }));
    const row = new Adw.ActionRow({title, subtitle});
    row.add_suffix(scale);
    return row;
}

/** A group that shows one warning line, hidden while there is nothing to say. */
function warningGroup() {
    const group = new Adw.PreferencesGroup({visible: false});
    const row = new Adw.ActionRow({icon_name: 'dialog-warning-symbolic'});
    row.add_css_class('warning');
    group.add(row);
    group.show = message => {
        row.title = message ?? '';
        group.visible = Boolean(message);
    };
    return group;
}

// --- Cooler LCD (on the OMEN-PC tab) ----------------------------------------------

function addLcdGroup(page, settings, window) {
    const group = new Adw.PreferencesGroup({
        title: 'Cooler LCD',
        description: 'The round screen on the CPU cooler.',
    });
    page.add(group);

    const enabledRow = new Adw.SwitchRow({title: 'Screen on', subtitle: 'Off shows a black screen'});
    settings.bind('lcd-enabled', enabledRow, 'active', Gio.SettingsBindFlags.DEFAULT);
    group.add(enabledRow);

    // Preview of the chosen image.
    const preview = new Gtk.Picture({
        width_request: 240,
        height_request: 240,
        can_shrink: true,
        halign: Gtk.Align.CENTER,
        margin_top: 12,
        margin_bottom: 12,
        overflow: Gtk.Overflow.HIDDEN,
    });
    preview.add_css_class('omen-lcd-preview'); // round, like the real screen
    const previewRow = new Adw.PreferencesRow({activatable: false});
    previewRow.set_child(preview);
    group.add(previewRow);

    // Image chooser.
    const imageRow = new Adw.ActionRow({title: 'Image'});
    const chooseButton = new Gtk.Button({label: 'Choose image…', valign: Gtk.Align.CENTER});
    const clearButton = new Gtk.Button({
        icon_name: 'edit-clear-symbolic',
        tooltip_text: 'Go back to the OMEN logo',
        valign: Gtk.Align.CENTER,
    });
    clearButton.add_css_class('flat');
    imageRow.add_suffix(clearButton);
    imageRow.add_suffix(chooseButton);
    group.add(imageRow);

    chooseButton.connect('clicked', () => {
        const filter = new Gtk.FileFilter({name: 'Images'});
        filter.add_pixbuf_formats();
        const filters = new Gio.ListStore({item_type: Gtk.FileFilter});
        filters.append(filter);
        const dialog = new Gtk.FileDialog({title: 'Choose an image for the LCD', filters});
        dialog.open(window, null, (_dialog, result) => {
            let file;
            try {
                file = dialog.open_finish(result);
            } catch {
                return; // the user pressed Cancel
            }
            const path = file.get_path();
            if (!path)
                return;
            settings.set_strv('lcd-recent-images',
                pushRecent(settings.get_strv('lcd-recent-images'), path));
            settings.set_string('lcd-image', path);
        });
    });
    clearButton.connect('clicked', () => settings.set_string('lcd-image', ''));

    // Recently used images: click one to show it again.
    const recentBox = new Gtk.Box({
        spacing: 8,
        halign: Gtk.Align.CENTER,
        margin_top: 8,
        margin_bottom: 8,
    });
    const recentRow = new Adw.PreferencesRow({activatable: false});
    recentRow.set_child(recentBox);
    group.add(recentRow);

    const rebuildRecent = () => {
        let child;
        while ((child = recentBox.get_first_child()))
            recentBox.remove(child);
        const recent = settings.get_strv('lcd-recent-images').slice(0, RECENT_IMAGES_SHOWN);
        recentRow.visible = recent.length > 1;
        for (const path of recent) {
            const thumbnail = new Gtk.Picture({
                width_request: THUMBNAIL_SIZE,
                height_request: THUMBNAIL_SIZE,
                can_shrink: true,
                content_fit: Gtk.ContentFit.COVER,
            });
            thumbnail.set_filename(path);
            const button = new Gtk.Button({
                child: thumbnail,
                tooltip_text: GLib.path_get_basename(path),
                overflow: Gtk.Overflow.HIDDEN,
            });
            button.add_css_class('flat');
            button.connect('clicked', () => settings.set_string('lcd-image', path));
            recentBox.append(button);
        }
    };

    // Fill or fit.
    const fitRow = new Adw.ComboRow({title: 'Fit', model: Gtk.StringList.new(FIT_LABELS)});
    fitRow.selected = Math.max(0, FIT_VALUES.indexOf(settings.get_string('lcd-fit')));
    fitRow.connect('notify::selected', () => {
        const value = FIT_VALUES[fitRow.selected];
        if (value !== settings.get_string('lcd-fit'))
            settings.set_string('lcd-fit', value);
    });
    group.add(fitRow);

    group.add(sliderRow(settings, 'lcd-brightness', 'Brightness'));

    // Keep the preview and labels in step with the settings.
    const refresh = () => {
        const path = settings.get_string('lcd-image');
        const fit = settings.get_string('lcd-fit');
        preview.set_filename(path || null);
        preview.content_fit = fit === 'fit' ? Gtk.ContentFit.CONTAIN : Gtk.ContentFit.COVER;
        imageRow.subtitle = path ? GLib.path_get_basename(path) : 'None (OMEN logo)';
        clearButton.sensitive = Boolean(path);
        fitRow.selected = Math.max(0, FIT_VALUES.indexOf(fit));
    };
    follow(settings, window, ['lcd-image', 'lcd-fit'], refresh);
    follow(settings, window, ['lcd-recent-images'], rebuildRecent);
}

// --- Device pages ---------------------------------------------------------------

/**
 * One group per lighting device: an effect chooser, an "all" colour and a
 * colour per zone. `device` comes from the service's status report:
 *   name, zones            real names, used as keys when saving
 *   label, zone_labels     what to show
 *   modes, default_mode    the effects on offer
 *   colourless_modes       effects that ignore colours (their rows are hidden)
 *   speed_modes            effects that move (they get a speed slider)
 *   range_modes            effects that pulse (they get dimmest/brightest sliders)
 * The rows follow the settings until the functions added to `cleanups` are called.
 */
function buildDeviceGroup(settings, device, cleanups) {
    const group = new Adw.PreferencesGroup({title: device.label ?? device.name});
    const zoneLabels = device.zone_labels ?? device.zones;
    const colourless = device.colourless_modes ?? [];
    const zoneKey = zone => `${device.name}/${zone}`;

    // Effect.
    const modeRow = new Adw.ComboRow({title: 'Effect', model: Gtk.StringList.new(device.modes)});
    const savedMode = getEntry(settings, 'device-modes', device.name,
        device.default_mode || DEFAULT_MODE);
    modeRow.selected = Math.max(0, device.modes.indexOf(savedMode));
    group.add(modeRow);

    // Extra sliders that only appear for effects that use them.
    const speedRow = deviceSliderRow(settings, 'device-speeds', device.name,
        'Speed', 'How fast the effect moves', 50, cleanups);
    const brightestRow = deviceSliderRow(settings, 'device-max-brightness', device.name,
        'Brightest', 'The top of each pulse', 100, cleanups);
    const dimmestRow = deviceSliderRow(settings, 'device-min-brightness', device.name,
        'Dimmest', 'The bottom of each pulse', 0, cleanups);
    [speedRow, brightestRow, dimmestRow].forEach(row => group.add(row));
    const speedModes = device.speed_modes ?? [];
    const rangeModes = device.range_modes ?? [];

    // One colour for everything, for the common case.
    const colourRows = [];
    const zoneButtons = [];
    const zoneColour = zone => getEntry(settings, 'zone-colours', zoneKey(zone), DEFAULT_COLOUR);
    let allButton = null;
    let syncing = false; // true while the buttons are being set to match the settings
    const colourButton = title => new Gtk.ColorDialogButton({
        dialog: new Gtk.ColorDialog({with_alpha: false, title}),
        valign: Gtk.Align.CENTER,
    });

    if (device.zones.length > 1) {
        allButton = colourButton('All');
        allButton.rgba = hexToRgba(zoneColour(device.zones[0]));
        allButton.connect('notify::rgba', () => {
            if (syncing)
                return; // only showing the first zone's colour, not choosing one
            // Save every zone in one go, then update the buttons to match.
            const hex = rgbaToHex(allButton.rgba);
            setEntries(settings, 'zone-colours',
                Object.fromEntries(device.zones.map(zone => [zoneKey(zone), hex])));
            zoneButtons.forEach(button => {
                if (rgbaToHex(button.rgba) !== hex)
                    button.rgba = allButton.rgba;
            });
        });
        const allRow = new Adw.ActionRow({title: 'All', subtitle: 'Set every colour below at once'});
        allRow.add_suffix(allButton);
        group.add(allRow);
        colourRows.push(allRow);
    }

    // A colour per zone.
    device.zones.forEach((zone, index) => {
        const button = colourButton(zoneLabels[index] ?? zone);
        button.rgba = hexToRgba(zoneColour(zone));
        button.connect('notify::rgba', () => {
            if (!syncing)
                setEntries(settings, 'zone-colours', {[zoneKey(zone)]: rgbaToHex(button.rgba)});
        });
        const row = new Adw.ActionRow({title: zoneLabels[index] ?? zone});
        row.add_suffix(button);
        group.add(row);
        colourRows.push(row);
        zoneButtons.push(button);
    });

    // Show only the rows the chosen effect actually uses.
    const showRowsFor = mode => {
        speedRow.visible = speedModes.includes(mode);
        brightestRow.visible = dimmestRow.visible = rangeModes.includes(mode);
        colourRows.forEach(row => (row.visible = !colourless.includes(mode)));
    };
    modeRow.connect('notify::selected', () => {
        const mode = device.modes[modeRow.selected];
        if (!syncing)
            setEntries(settings, 'device-modes', {[device.name]: mode});
        showRowsFor(mode);
    });
    showRowsFor(device.modes[modeRow.selected]);

    // Follow changes made elsewhere ("Load" of the Default, another front end).
    const sameColour = (button, hex) => rgbaToHex(button.rgba) === rgbaToHex(hexToRgba(hex));
    cleanups.push(watch(settings, ['device-modes', 'zone-colours'], () => {
        syncing = true;
        const index = device.modes.indexOf(getEntry(settings, 'device-modes', device.name,
            device.default_mode || DEFAULT_MODE));
        if (index >= 0 && index !== modeRow.selected)
            modeRow.selected = index;
        device.zones.forEach((zone, i) => {
            if (!sameColour(zoneButtons[i], zoneColour(zone)))
                zoneButtons[i].rgba = hexToRgba(zoneColour(zone));
        });
        if (allButton && !sameColour(allButton, zoneColour(device.zones[0])))
            allButton.rgba = hexToRgba(zoneColour(device.zones[0]));
        syncing = false;
    }));

    return group;
}

/** What is wrong with a device right now, or null. */
function deviceProblem(status, device) {
    const report = deviceStatus(status, device.id);
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
function buildDevicePage(settings, window, device) {
    const page = new Adw.PreferencesPage({title: device.label, icon_name: device.icon});

    const warning = warningGroup();
    page.add(warning);

    if (device.lcd)
        addLcdGroup(page, settings, window);

    const group = new Adw.PreferencesGroup({title: 'Lights'});
    page.add(group);
    group.add(deviceSwitchRow(settings, 'device-enabled', device.id, true, {title: 'Lights on'}));
    const pageCleanups = [];
    group.add(deviceSliderRow(settings, 'device-brightness', device.id, 'Brightness', null, 100,
        pageCleanups));

    // Lighting groups are rebuilt only when what the hub found really changes.
    let lightGroups = [];
    const groupCleanups = [];
    let builtFor = null;
    window.connect('close-request', () => {
        runAll(pageCleanups);
        runAll(groupCleanups);
        return false;
    });
    const refresh = () => {
        const status = readStatus(settings);
        warning.show(deviceProblem(status, device));
        const lights = deviceStatus(status, device.id)?.lights ?? [];
        const signature = JSON.stringify(lights);
        if (signature === builtFor)
            return;
        builtFor = signature;
        runAll(groupCleanups);
        lightGroups.forEach(g => page.remove(g));
        lightGroups = lights.map(light => buildDeviceGroup(settings, light, groupCleanups));
        lightGroups.forEach(g => page.add(g));
    };
    follow(settings, window, ['status'], refresh);

    return page;
}

// --- Auto page ------------------------------------------------------------------

function buildAutoPage(settings, window, devices) {
    const page = new Adw.PreferencesPage({title: 'Auto', icon_name: 'starred-symbolic'});

    const group = new Adw.PreferencesGroup({
        title: 'Auto mode',
        description: 'Match your devices to the app you are using. With nothing in focus, ' +
            'or an app without an icon, they match the desktop: its wallpaper and colour. ' +
            'Your normal settings show while this window has focus.',
    });
    page.add(group);

    // The master switch is on while every device follows; flipping it sets them all.
    const allRow = new Adw.SwitchRow({title: 'Auto mode', subtitle: 'Every device'});
    const everyOn = () => {
        const auto = settings.get_value('auto-devices').deepUnpack();
        return devices.every(device => auto[device.id]);
    };
    allRow.connect('notify::active', () => {
        if (allRow.active === everyOn())
            return;
        settings.set_value('auto-devices', new GLib.Variant('a{sb}',
            Object.fromEntries(devices.map(device => [device.id, allRow.active]))));
    });
    group.add(allRow);

    const devicesGroup = new Adw.PreferencesGroup({title: 'Per device'});
    page.add(devicesGroup);
    for (const device of devices) {
        const row = deviceSwitchRow(settings, 'auto-devices', device.id, false,
            {title: device.label});
        devicesGroup.add(row);
        if (!device.lcd)
            continue;
        const lightsRow = new Adw.SwitchRow({
            title: 'Lights follow the app',
            subtitle: 'Uses the main colour of the app\u2019s icon',
        });
        settings.bind('auto-lights', lightsRow, 'active', Gio.SettingsBindFlags.DEFAULT);
        const lcdRow = new Adw.SwitchRow({
            title: 'LCD shows the app',
            subtitle: 'Puts the app\u2019s icon on the cooler screen',
        });
        settings.bind('auto-lcd', lcdRow, 'active', Gio.SettingsBindFlags.DEFAULT);
        for (const sub of [lightsRow, lcdRow]) {
            sub.add_css_class('indent');
            row.bind_property('active', sub, 'sensitive', GObject.BindingFlags.SYNC_CREATE);
            devicesGroup.add(sub);
        }
    }

    // What auto mode last picked up, as reported by the hub.
    const nowRow = new Adw.ActionRow({title: 'Now following'});
    const swatch = new Gtk.ColorDialogButton({
        dialog: new Gtk.ColorDialog({with_alpha: false}),
        valign: Gtk.Align.CENTER,
        sensitive: false, // display only
    });
    nowRow.add_suffix(swatch);
    group.add(nowRow);

    follow(settings, window, ['status', 'auto-devices'], () => {
        const on = everyOn();
        if (allRow.active !== on)
            allRow.active = on;
        const anyOn = Object.values(settings.get_value('auto-devices').deepUnpack()).some(v => v);
        const auto = readStatus(settings).auto ?? {};
        nowRow.visible = anyOn;
        nowRow.subtitle = auto.app
            ? auto.app.replace(/\.desktop$/, '')
            : auto.colour ? 'Nothing in focus (desktop look)' : 'Your normal settings';
        swatch.visible = Boolean(auto.colour);
        if (auto.colour)
            swatch.rgba = hexToRgba(auto.colour);
    });

    page.add(buildDefaultGroup(settings, window));
    return page;
}

/** The Default look: what every device shows once the hub stops. */
function buildDefaultGroup(settings, window) {
    const group = new Adw.PreferencesGroup({
        title: 'Default look',
        description: 'Shown on every device when RGB Hub stops (shutting down, logging out, ' +
            'or the service being stopped), so whatever starts next shows it too. ' +
            'The keyboard keeps it by itself; the cooler screen keeps the last picture sent. With no Default saved: purple lights and Spike on the screen.',
    });
    const saveRow = new Adw.ActionRow({
        title: 'Save current look as Default',
        subtitle: 'Each device\u2019s own settings, as on its tab',
    });
    const saveButton = new Gtk.Button({label: 'Save', valign: Gtk.Align.CENTER});
    saveButton.connect('clicked', () => settings.set_string('default-action', 'save'));
    saveRow.add_suffix(saveButton);
    group.add(saveRow);

    const loadRow = new Adw.ActionRow({
        title: 'Go back to the Default',
        subtitle: 'Makes the Default each device\u2019s own settings again',
    });
    const loadButton = new Gtk.Button({label: 'Load', valign: Gtk.Align.CENTER});
    loadButton.connect('clicked', () => settings.set_string('default-action', 'load'));
    loadRow.add_suffix(loadButton);
    group.add(loadRow);

    follow(settings, window, ['status'], () => {
        const status = readStatus(settings);
        const running = status.devices !== undefined;
        saveButton.sensitive = running;
        loadButton.sensitive = running && Boolean(status['has-default']);
        saveRow.subtitle = running
            ? 'Each device\u2019s own settings, as on its tab'
            : 'Needs the RGB Hub service running';
    });
    return group;
}

/** Extra styling for this window only. */
function loadStyles() {
    const provider = new Gtk.CssProvider();
    provider.load_from_string(
        '.omen-lcd-preview { border-radius: 9999px; background: black; }\n' +
        'row.indent { padding-left: 24px; }');
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION);
}

// --- entry point ----------------------------------------------------------------

export default class RgbHubPreferences extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        const settings = this.getSettings();
        loadStyles();
        window.set_search_enabled(false); // short pages; nothing to search
        window.set_default_size(560, 760);
        const devices = hubDevices(settings);
        window.add(buildAutoPage(settings, window, devices));
        for (const device of devices)
            window.add(buildDevicePage(settings, window, device));
    }
}

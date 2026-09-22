/** @jsxImportSource preact */
import { Fragment } from "preact";
import type { JSX } from "preact";
import { useCallback, useEffect, useMemo, useRef, useState } from "preact/hooks";

import { getJson, patchJson, postForm, postJson } from "./lib/api";
import { useDashboardPolling } from "./hooks/useDashboardPolling";
import { useLibraryData } from "./hooks/useLibraryData";
import { useLibraryRecents, type RecentEntry } from "./hooks/useLibraryRecents";
import { useNetworkStatus } from "./hooks/useNetworkStatus";
import { usePlaybackActions } from "./hooks/usePlaybackActions";
import { useSettingsMutations } from "./hooks/useSettingsMutations";
import { useTheme } from "./hooks/useTheme";
import { useToasts } from "./hooks/useToasts";
import type {
  AlarmFormState,
  AlarmSummary,
  AppBootstrap,
  ArtistDrilldown,
  AsyncStatus,
  CollectionState,
  DashboardData,
  LibraryItem,
  LibrarySection,
  PlayFormState,
  PlaybackQueuePayload,
  PrimaryFlowSnapshot,
  SettingsData,
  SleepFormState,
  SpotifyDevice,
  SpotifySettingsPayload,
  SpotifyProfile,
  SurfaceName,
  ToastItem
} from "./lib/types";
import {
  createAlarmFormModel,
  createPlayFormModel,
  createSleepFormModel,
  getPreferredDevice,
  SLEEP_DURATION_PRESETS,
  toPrimaryFlowSnapshot
} from "./lib/view_models";

const LIBRARY_SECTIONS: LibrarySection[] = ["playlists", "albums", "tracks", "artists", "recent", "top", "search"];
const DURATION_OPTIONS = SLEEP_DURATION_PRESETS;
const LAST_DEVICE_STORAGE_KEY = "spotipi.last_device_id";
const FOCUSABLE_SELECTOR = [
  "a[href]",
  "area[href]",
  "button:not([disabled])",
  "input:not([type='hidden']):not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])"
].join(",");

interface AccountLoadState {
  status:
    | "idle"
    | "loading"
    | "connected"
    | "missing_credentials"
    | "auth_required"
    | "offline"
    | "error";
  profile?: SpotifyProfile;
  errorMessage?: string;
}

interface SpotifyCredentialFormState {
  clientId: string;
  clientSecret: string;
  refreshToken: string;
  username: string;
}

interface SpotifyCredentialTouchedState {
  clientId: boolean;
  clientSecret: boolean;
  refreshToken: boolean;
  username: boolean;
}

type TranslateFn = (
  key: string,
  fallback: string,
  params?: Record<string, string | number>
) => string;

const ICONS: Record<string, string> = {
  alarm: "M12 3a7 7 0 0 0-7 7v4.6l-1.8 2.4A1 1 0 0 0 4 19h16a1 1 0 0 0 .8-1.6L19 14.6V10a7 7 0 0 0-7-7Zm0 18a2.3 2.3 0 0 0 2.2-1.7H9.8A2.3 2.3 0 0 0 12 21Z",
  sleep: "M13.5 2.2a8.9 8.9 0 1 0 8.3 12.2A9.8 9.8 0 0 1 13.5 2.2Z",
  play: "M8 6.2v11.6a1 1 0 0 0 1.5.9l8.8-5.8a1 1 0 0 0 0-1.8L9.5 5.3a1 1 0 0 0-1.5.9Z",
  pause: "M7 5h4v14H7zm6 0h4v14h-4z",
  next: "M6.5 7.1v9.8l7-4.9-7-4.9Zm8.4 0H17v9.8h-2.1zm3.6 0h2v9.8h-2z",
  previous: "M17.5 7.1v9.8l-7-4.9 7-4.9Zm-8.4 0H7v9.8h2.1zm-5.1 0h2v9.8h-2z",
  settings: "M19.4 13a7.7 7.7 0 0 0 .1-1l2-1.5-1.8-3.1-2.4.8a7.8 7.8 0 0 0-1.7-1l-.3-2.5H8.7l-.3 2.5a7.8 7.8 0 0 0-1.7 1l-2.4-.8-1.8 3.1 2 1.5a7.7 7.7 0 0 0 0 2l-2 1.5 1.8 3.1 2.4-.8a7.8 7.8 0 0 0 1.7 1l.3 2.5h6.6l.3-2.5a7.8 7.8 0 0 0 1.7-1l2.4.8 1.8-3.1-2-1.5ZM12 15.2A3.2 3.2 0 1 1 12 8.8a3.2 3.2 0 0 1 0 6.4Z",
  library: "M8 5.6v10.1a3.4 3.4 0 1 0 2 3V9.8h8v-4.2H8Z",
  volume: "M4 9.5v5h3.2L11 18V6L7.2 9.5H4Zm10.7-2.8v2.2a3.8 3.8 0 0 1 0 6.2v2.2a5.9 5.9 0 0 0 0-10.6Zm2.9-2.7v2.1a7.7 7.7 0 0 1 0 11.8V20a9.8 9.8 0 0 0 0-16Z",
  note: "M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6Z",
  device: "M5 4h14a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-5l2 3H8l2-3H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z",
  refresh: "M18.4 8A7 7 0 1 0 19 12h-2a5 5 0 1 1-1.4-3.5L13 11h7V4l-1.6 4Z",
  close: "M6.7 5.3 12 10.6l5.3-5.3 1.4 1.4L13.4 12l5.3 5.3-1.4 1.4L12 13.4l-5.3 5.3-1.4-1.4L10.6 12 5.3 6.7l1.4-1.4Z",
  check: "M9.5 16.2 5.3 12l1.4-1.4 2.8 2.8 7.8-7.8 1.4 1.4-9.2 9.2Z",
  eye: "M12 5c-5 0-9 4-10 7 1 3 5 7 10 7s9-4 10-7c-1-3-5-7-10-7Zm0 11a4 4 0 1 1 0-8 4 4 0 0 1 0 8Zm0-2.2a1.8 1.8 0 1 0 0-3.6 1.8 1.8 0 0 0 0 3.6Z",
  "eye-off":
    "m4.8 4 14.4 16-1.4 1.4-2.7-3A12.5 12.5 0 0 1 12 19c-5 0-9-4-10-7 .7-2 2.3-4 4.7-5.6L3.4 2.6 4.8 4Zm8.7 9.7-3.3-3.6a2 2 0 0 0 3.3 3.6ZM12 5c5 0 9 4 10 7-.5 1.3-1.4 2.8-2.9 4.1l-1.4-1.6A9.6 9.6 0 0 0 20 12c-1-2.3-4.2-5-8-5-1 0-1.9.2-2.7.4L7.8 5.8A12.2 12.2 0 0 1 12 5Zm-.2 2.8a4 4 0 0 1 4 4c0 .5-.1 1-.3 1.4l-1.6-1.7a2.1 2.1 0 0 0-2-2l-1.7-1.8c.5-.1 1-.2 1.6-.2Z",
  lightning: "M13 2 5 13h5l-1 9 8-11h-5l1-9Z",
  arrow: "m8 5 8 7-8 7V5Z",
  chevron: "M12 15.4 4.6 8 6 6.6l6 6 6-6 1.4 1.4L12 15.4Z",
  spotify: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Zm4.5 14.5a.7.7 0 0 1-1 .2c-2.7-1.7-6.1-2.1-10-.9a.7.7 0 1 1-.4-1.4c4.3-1.3 8.1-.9 11.2 1.1.3.2.4.7.2 1Zm1.4-3a.9.9 0 0 1-1.2.3c-3.1-1.9-7.8-2.5-11.5-1.2a.9.9 0 0 1-.6-1.7c4.3-1.4 9.5-.8 13.1 1.4.4.3.6.8.2 1.2Zm.1-3.2C14.2 8.1 8 7.8 4.8 8.8A1 1 0 0 1 4.2 7c3.8-1.2 10.7-.9 14.8 1.7a1 1 0 1 1-1 1.6Z"
};

function localized(language: string, en: string, de: string): string {
  return language === "de" ? de : en;
}

function stripMarkup(value: string): string {
  return value.replace(/<[^>]+>/g, "").replace(/\s+/g, " ").trim();
}

function createTranslator(
  language: string,
  translations: Record<string, string>
): TranslateFn {
  return (key, fallback, params) => {
    const template = stripMarkup(translations[key] || fallback);
    if (!params) {
      return template;
    }

    return template.replace(/\{(\w+)\}/g, (_, token) => {
      if (token in params) {
        return String(params[token]);
      }
      return `{${token}}`;
    });
  };
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function normalizeImageUrl(candidate: string | null | undefined): string | null {
  if (typeof candidate !== "string" || !candidate.trim()) {
    return null;
  }

  try {
    const parsed = new URL(candidate, window.location.origin);
    if (parsed.protocol === "https:" || parsed.protocol === "http:") {
      return parsed.href;
    }
  } catch {
    return null;
  }

  return null;
}

function isValidTimeInput(value: string): boolean {
  return /^([01]\d|2[0-3]):([0-5]\d)$/.test(value);
}

function hasPassedToday(value: string): boolean {
  if (!isValidTimeInput(value)) {
    return false;
  }
  const [hour, minute] = value.split(":").map((segment) => Number(segment));
  const now = new Date();
  const selected = new Date(now);
  selected.setHours(hour, minute, 0, 0);
  return selected.getTime() <= now.getTime();
}

function readLastDeviceId(): string {
  try {
    return window.localStorage.getItem(LAST_DEVICE_STORAGE_KEY) || "";
  } catch {
    return "";
  }
}

function writeLastDeviceId(deviceId: string): void {
  if (!deviceId) {
    return;
  }
  try {
    window.localStorage.setItem(LAST_DEVICE_STORAGE_KEY, deviceId);
  } catch {
    // Ignore quota/storage errors on constrained devices.
  }
}

function getFocusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter((element) => {
    if (element.hasAttribute("disabled") || element.getAttribute("aria-hidden") === "true") {
      return false;
    }
    return element.tabIndex >= 0 && element.offsetParent !== null;
  });
}

function buildRecentMeta(item: LibraryItem, language: string): string {
  if (item.artist) {
    return item.artist;
  }
  if (item.track_count) {
    return localized(language, `${item.track_count} tracks`, `${item.track_count} Titel`);
  }
  return item.type || "";
}

function formatTimeLabel(value: string, language: string): string {
  if (!value) {
    return "--:--";
  }

  const [hour, minute] = value.split(":");
  return `${hour?.padStart(2, "0") || "--"}:${minute?.padStart(2, "0") || "--"}`;
}

function formatDateTime(now: Date, language: string): { date: string; time: string } {
  return {
    date: new Intl.DateTimeFormat(language, {
      weekday: "short",
      month: "short",
      day: "numeric"
    }).format(now),
    time: new Intl.DateTimeFormat(language, {
      hour: "2-digit",
      minute: "2-digit"
    }).format(now)
  };
}

function formatCountdown(value: number | undefined, language: string): string {
  const totalSeconds = Math.max(0, Math.floor(value || 0));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;

  if (hours > 0) {
    return language === "de"
      ? `${hours} Std ${minutes} Min`
      : `${hours}h ${minutes}m`;
  }

  if (minutes > 0) {
    return language === "de"
      ? `${minutes} Min ${seconds.toString().padStart(2, "0")} Sek`
      : `${minutes}m ${seconds.toString().padStart(2, "0")}s`;
  }

  return language === "de" ? `${seconds} Sek` : `${seconds}s`;
}

function createEmptySpotifyCredentialForm(): SpotifyCredentialFormState {
  return {
    clientId: "",
    clientSecret: "",
    refreshToken: "",
    username: ""
  };
}

function createEmptySpotifyCredentialTouched(): SpotifyCredentialTouchedState {
  return {
    clientId: false,
    clientSecret: false,
    refreshToken: false,
    username: false
  };
}

function toAccountLoadState(
  spotifySettings: SpotifySettingsPayload,
  language: string
): AccountLoadState {
  const connection = spotifySettings.connection;
  const fallbackMessage = localized(language, "Spotify account unavailable", "Spotify-Konto nicht verfügbar");

  if (connection.status === "connected") {
    return {
      status: "connected",
      profile: connection.profile || undefined,
      errorMessage: connection.message
    };
  }

  if (connection.status === "missing_credentials") {
    return {
      status: "missing_credentials",
      errorMessage: connection.message || fallbackMessage
    };
  }

  if (connection.status === "auth_required") {
    return {
      status: "auth_required",
      errorMessage: connection.message || fallbackMessage
    };
  }

  if (connection.status === "offline") {
    return {
      status: "offline",
      errorMessage: connection.message || fallbackMessage
    };
  }

  return {
    status: "error",
    errorMessage: connection.message || fallbackMessage
  };
}

function icon(name: string, className = "icon"): JSX.Element {
  return (
    <svg class={className} viewBox="0 0 24 24" aria-hidden="true">
      <path d={ICONS[name]} />
    </svg>
  );
}

interface StatusPillProps {
  tone: "success" | "warning" | "muted" | "danger";
  label: string;
}

function StatusPill({ tone, label }: StatusPillProps) {
  return <span class={`status-pill status-pill-${tone}`}>{label}</span>;
}

interface ToastStackProps {
  items: ToastItem[];
  onDismiss: (id: number) => void;
  dismissLabel: string;
}

function ToastStack({ items, onDismiss, dismissLabel }: ToastStackProps) {
  // No live region on the container: that + aria-atomic="true" made assistive tech
  // re-announce the entire stack on every add/auto-dismiss. Instead each toast is its
  // own atomic live region — errors assertive (role="alert", interrupts) so failures
  // aren't missed, info/success polite (role="status").
  return (
    <div class="toast-stack">
      {items.map((item) => (
        <div
          class={`toast toast-${item.type}`}
          key={item.id}
          role={item.type === "error" ? "alert" : "status"}
          aria-live={item.type === "error" ? "assertive" : "polite"}
          aria-atomic="true"
        >
          <span>{item.message}</span>
          <button
            type="button"
            class="icon-button"
            aria-label={dismissLabel}
            onClick={() => onDismiss(item.id)}
          >
            {icon("close")}
          </button>
        </div>
      ))}
    </div>
  );
}

interface SheetProps {
  id: string;
  title: string;
  subtitle?: string;
  open: boolean;
  onClose: () => void;
  closeLabel: string;
  variant?: "default" | "settings";
  children: JSX.Element;
}

function Sheet({ id, title, subtitle, open, onClose, closeLabel, variant = "default", children }: SheetProps) {
  const dialogRef = useRef<HTMLElement | null>(null);
  const openerRef = useRef<HTMLElement | null>(null);
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!open) {
      return;
    }

    openerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;

    const dialog = dialogRef.current;
    if (!dialog) {
      return;
    }

    const initialTarget =
      dialog.querySelector<HTMLElement>("[data-sheet-initial-focus]") || getFocusableElements(dialog)[0];
    if (initialTarget) {
      initialTarget.focus();
    } else {
      dialog.focus();
    }

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }

      if (event.key !== "Tab") {
        return;
      }

      const focusables = getFocusableElements(dialog);
      if (focusables.length === 0) {
        event.preventDefault();
        dialog.focus();
        return;
      }

      const active = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      const currentIndex = active ? focusables.indexOf(active) : -1;

      if (event.shiftKey) {
        event.preventDefault();
        const previousIndex = currentIndex <= 0 ? focusables.length - 1 : currentIndex - 1;
        focusables[previousIndex]?.focus();
        return;
      }

      event.preventDefault();
      const nextIndex = currentIndex === -1 || currentIndex >= focusables.length - 1 ? 0 : currentIndex + 1;
      focusables[nextIndex]?.focus();
    };

    document.addEventListener("keydown", onKeyDown);

    return () => {
      document.removeEventListener("keydown", onKeyDown);
      const opener = openerRef.current;
      if (opener && document.contains(opener)) {
        opener.focus();
      }
    };
  }, [open]);

  if (!open) {
    return null;
  }

  return (
    <div class="sheet-backdrop" onClick={onClose}>
      <section
        id={id}
        ref={dialogRef}
        class={`sheet sheet-${variant}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${id}-title`}
        tabIndex={-1}
        onClick={(event) => event.stopPropagation()}
      >
        <header class="sheet-header">
          <div>
            <p class="sheet-eyebrow">SpotiPi</p>
            <h2 id={`${id}-title`}>{title}</h2>
            {subtitle ? <p class="sheet-subtitle">{subtitle}</p> : null}
          </div>
          <button type="button" class="icon-button" aria-label={closeLabel} onClick={onClose}>
            {icon("close")}
          </button>
        </header>
        <div class="sheet-body">{children}</div>
      </section>
    </div>
  );
}

interface ToggleFieldProps {
  label: string;
  description?: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}

function ToggleField({ label, description, checked, onChange }: ToggleFieldProps) {
  return (
    <label class="toggle-field">
      <span class="toggle-copy">
        <span class="toggle-title">{label}</span>
        {description ? <span class="toggle-description">{description}</span> : null}
      </span>
      <span class="toggle-control">
        <input
          type="checkbox"
          checked={checked}
          onChange={(event) => onChange((event.currentTarget as HTMLInputElement).checked)}
        />
        <span class="toggle-track" aria-hidden="true" />
      </span>
    </label>
  );
}

// Weekday order is 0=Monday … 6=Sunday, matching the backend config schema.
const WEEKDAY_ORDER = [0, 1, 2, 3, 4, 5, 6] as const;
const WEEKDAYS_PRESET_DAILY = [0, 1, 2, 3, 4, 5, 6];
const WEEKDAYS_PRESET_WORKDAYS = [0, 1, 2, 3, 4];

function weekdayShortLabel(day: number, language: string): string {
  const de = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
  const en = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  return (language === "de" ? de : en)[day] ?? String(day);
}

function sameWeekdaySet(a: number[], b: number[]): boolean {
  if (a.length !== b.length) return false;
  const sortedA = [...a].sort((x, y) => x - y);
  const sortedB = [...b].sort((x, y) => x - y);
  return sortedA.every((value, index) => value === sortedB[index]);
}

function formatWeekdaySummary(value: number[], language: string): string {
  if (value.length === 0) {
    return localized(language, "Once", "Einmal");
  }
  if (sameWeekdaySet(value, WEEKDAYS_PRESET_DAILY)) {
    return localized(language, "Daily", "Täglich");
  }
  if (sameWeekdaySet(value, WEEKDAYS_PRESET_WORKDAYS)) {
    return localized(language, "Weekdays", "Werktags");
  }
  const selected = new Set(value);
  return WEEKDAY_ORDER.filter((day) => selected.has(day))
    .map((day) => weekdayShortLabel(day, language))
    .join(", ");
}

interface WeekdayPickerProps {
  value: number[];
  language: string;
  onChange: (weekdays: number[]) => void;
}

function WeekdayPicker({ value, language, onChange }: WeekdayPickerProps) {
  const selected = new Set(value);
  const isDaily = sameWeekdaySet(value, WEEKDAYS_PRESET_DAILY);
  const isWorkdays = sameWeekdaySet(value, WEEKDAYS_PRESET_WORKDAYS);
  const summary = formatWeekdaySummary(value, language);

  function toggleDay(day: number) {
    const next = selected.has(day)
      ? value.filter((existing) => existing !== day)
      : [...value, day].sort((a, b) => a - b);
    onChange(next);
  }

  function applyPreset(preset: number[]) {
    // Tapping the active preset clears it back to a single-use alarm.
    onChange(sameWeekdaySet(value, preset) ? [] : [...preset]);
  }

  return (
    <div class="weekday-picker">
      <div class="field-label-row">
        <label class="field-label">{localized(language, "Repeat", "Wiederholen")}</label>
        <strong>{summary}</strong>
      </div>
      <div class="weekday-chips" role="group" aria-label={localized(language, "Repeat on weekdays", "An Wochentagen wiederholen")}>
        {WEEKDAY_ORDER.map((day) => {
          const active = selected.has(day);
          return (
            <button
              key={day}
              type="button"
              class={`weekday-chip ${active ? "is-active" : ""}`}
              aria-pressed={active}
              onClick={() => toggleDay(day)}
            >
              {weekdayShortLabel(day, language)}
            </button>
          );
        })}
      </div>
      <div class="weekday-presets">
        <button
          type="button"
          class={`weekday-preset ${isDaily ? "is-active" : ""}`}
          aria-pressed={isDaily}
          onClick={() => applyPreset(WEEKDAYS_PRESET_DAILY)}
        >
          {localized(language, "Daily", "Täglich")}
        </button>
        <button
          type="button"
          class={`weekday-preset ${isWorkdays ? "is-active" : ""}`}
          aria-pressed={isWorkdays}
          onClick={() => applyPreset(WEEKDAYS_PRESET_WORKDAYS)}
        >
          {localized(language, "Weekdays", "Werktags")}
        </button>
      </div>
    </div>
  );
}

interface DevicePickerProps {
  title: string;
  devices: SpotifyDevice[];
  selectedKey: string;
  status: string;
  offline: boolean;
  onSelect: (device: SpotifyDevice) => void;
  onRefresh: () => void;
  t: TranslateFn;
  language: string;
}

interface CustomSelectOption {
  value: string;
  label: string;
}

interface CustomSelectProps {
  value: string;
  options: CustomSelectOption[];
  onChange: (value: string) => void;
  id?: string;
}

/**
 * Shared keyboard + dismissal behaviour for the custom listbox dropdowns
 * (CustomSelect, DevicePicker). The markup already advertised role="listbox"/
 * "option" but implemented none of the listbox keyboard contract, so keyboard /
 * screen-reader users could open the list but not dismiss it (only mousedown-outside
 * closed it). This adds: Escape to close with focus returned to the trigger,
 * ArrowDown on the trigger to open, ArrowUp/Down to move between options, focus moved
 * to the selected option on open, and focusout-based dismissal when focus leaves.
 */
function useListboxDropdown() {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    // pointerdown, not mousedown: iOS Safari only synthesizes mousedown for taps on
    // "clickable" elements, so taps on plain background would never close the list.
    function handleOutside(e: PointerEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("pointerdown", handleOutside);
    return () => document.removeEventListener("pointerdown", handleOutside);
  }, [open]);

  // On open, move focus to the selected option (or the first) so keyboard users
  // land inside the list instead of being stranded on the trigger.
  useEffect(() => {
    if (!open) return;
    const list = containerRef.current?.querySelector('[role="listbox"]');
    if (!list) return;
    const selected = list.querySelector<HTMLButtonElement>('[role="option"][aria-selected="true"]');
    const first = list.querySelector<HTMLButtonElement>('[role="option"]');
    (selected ?? first)?.focus();
  }, [open]);

  const close = useCallback((focusTrigger: boolean) => {
    setOpen(false);
    if (focusTrigger) {
      // Defer until after the list is removed from the DOM. Focusing synchronously
      // races the re-render that unmounts the focused option, which can bounce focus
      // to <body> before our focus() lands on some browsers.
      requestAnimationFrame(() => triggerRef.current?.focus());
    }
  }, []);

  const handleListKeyDown = useCallback((e: JSX.TargetedKeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Escape") {
      e.preventDefault();
      // Close only the dropdown, not the enclosing sheet (whose document-level
      // Escape handler would otherwise also fire). A second Escape closes the sheet.
      e.stopPropagation();
      close(true);
      return;
    }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const list = containerRef.current?.querySelector('[role="listbox"]');
      if (!list) return;
      const options = Array.from(list.querySelectorAll<HTMLButtonElement>('[role="option"]'));
      if (!options.length) return;
      const current = options.findIndex((option) => option === document.activeElement);
      const next = e.key === "ArrowDown"
        ? (current + 1) % options.length
        : (current <= 0 ? options.length - 1 : current - 1);
      options[next].focus();
    }
  }, [close]);

  const handleTriggerKeyDown = useCallback((e: JSX.TargetedKeyboardEvent<HTMLButtonElement>) => {
    if (e.key === "ArrowDown" && !open) {
      e.preventDefault();
      setOpen(true);
    } else if (e.key === "Escape" && open) {
      e.preventDefault();
      e.stopPropagation();
      close(true);
    }
  }, [open, close]);

  const handleFocusOut = useCallback((e: JSX.TargetedFocusEvent<HTMLDivElement>) => {
    const nextTarget = e.relatedTarget as Node | null;
    // Belt and braces for iOS: a tap that blurs into nothing (no relatedTarget) must
    // not close the list either; outside taps are closed by pointerdown above.
    if (!nextTarget || containerRef.current?.contains(nextTarget)) {
      return;
    }
    setOpen(false);
  }, []);

  // Safari (macOS and iOS) does not focus a clicked/tapped <button>; focus falls
  // back to the nearest focusable ancestor (the sheet), so the focused option fires
  // focusout -> close before the click lands: an option tap is lost, a trigger tap
  // closes and then re-toggles open. Suppressing the mousedown default on the list
  // keeps focus where it is; click (and keyboard) still work.
  const suppressFocusSteal = useCallback((e: JSX.TargetedMouseEvent<HTMLElement>) => {
    e.preventDefault();
  }, []);

  // The trigger still needs focus (its keydown handles Escape before the open effect
  // has moved focus into the list), so focus it explicitly — Safari won't.
  const handleTriggerMouseDown = useCallback((e: JSX.TargetedMouseEvent<HTMLButtonElement>) => {
    e.preventDefault();
    triggerRef.current?.focus();
  }, []);

  return {
    open,
    setOpen,
    close,
    containerRef,
    triggerRef,
    handleListKeyDown,
    handleTriggerKeyDown,
    handleFocusOut,
    suppressFocusSteal,
    handleTriggerMouseDown,
  };
}

function CustomSelect({ value, options, onChange, id }: CustomSelectProps) {
  const {
    open,
    setOpen,
    close,
    containerRef,
    triggerRef,
    handleListKeyDown,
    handleTriggerKeyDown,
    handleFocusOut,
    suppressFocusSteal,
    handleTriggerMouseDown,
  } = useListboxDropdown();

  const selected = options.find((o) => o.value === value);

  return (
    <div class="device-dropdown" ref={containerRef} id={id} onFocusOut={handleFocusOut}>
      <button
        ref={triggerRef}
        type="button"
        class={`field-input device-dropdown-trigger ${open ? "is-open" : ""}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={handleTriggerKeyDown}
        onMouseDown={handleTriggerMouseDown}
      >
        <span>{selected?.label ?? value}</span>
        <span class={`device-dropdown-chevron ${open ? "is-open" : ""}`}>
          {icon("chevron")}
        </span>
      </button>
      {open ? (
        <div class="device-dropdown-list" role="listbox" onKeyDown={handleListKeyDown} onMouseDown={suppressFocusSteal}>
          {options.map((opt) => {
            const isSelected = opt.value === value;
            return (
              <button
                key={opt.value}
                type="button"
                role="option"
                aria-selected={isSelected}
                class={`device-dropdown-option ${isSelected ? "is-selected" : ""}`}
                onClick={() => {
                  onChange(opt.value);
                  close(true);
                }}
              >
                <span class="device-dropdown-option-name">{opt.label}</span>
                {isSelected ? icon("check", "icon device-dropdown-check") : null}
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

function DevicePicker({
  title,
  devices,
  selectedKey,
  status,
  offline,
  onSelect,
  onRefresh,
  t,
  language
}: DevicePickerProps) {
  const {
    open,
    setOpen,
    close,
    containerRef,
    triggerRef,
    handleListKeyDown,
    handleTriggerKeyDown,
    handleFocusOut,
    suppressFocusSteal,
    handleTriggerMouseDown,
  } = useListboxDropdown();

  const emptyMessage = offline
    ? t("status_offline", localized(language, "Offline mode", "Offline-Modus"))
    : status === "auth_required"
      ? t("status_auth_required", localized(language, "Spotify sign-in required", "Spotify-Anmeldung erforderlich"))
      : status === "pending"
        ? t("loading_devices", localized(language, "Loading speakers...", "Lautsprecher werden geladen..."))
        : status === "error"
          ? t("speaker_error", localized(language, "Error loading speakers", "Fehler beim Laden der Lautsprecher"))
          : t("no_devices_found", localized(language, "No speakers found", "Keine Lautsprecher gefunden"));

  const selectedDevice = devices.find(
    (d) => d.id === selectedKey || d.name === selectedKey
  );

  return (
    <div class="field-group">
      <label class="field-label">{title}</label>
      {devices.length === 0 ? (
        <div class="state-card state-card-muted">
          <p>{emptyMessage}</p>
        </div>
      ) : (
        <div class="device-select-row" ref={containerRef} onFocusOut={handleFocusOut}>
          <div class="device-dropdown">
            <button
              ref={triggerRef}
              type="button"
              class={`field-input device-dropdown-trigger ${open ? "is-open" : ""}`}
              aria-haspopup="listbox"
              aria-expanded={open}
              onClick={() => setOpen((v) => !v)}
              onKeyDown={handleTriggerKeyDown}
              onMouseDown={handleTriggerMouseDown}
            >
              <span class={selectedDevice ? "" : "device-dropdown-placeholder"}>
                {selectedDevice
                  ? selectedDevice.name
                  : localized(language, "Choose a speaker", "Lautsprecher wählen")}
              </span>
              {selectedDevice?.is_active ? (
                <span class="device-dropdown-active-dot" />
              ) : null}
              <span class={`device-dropdown-chevron ${open ? "is-open" : ""}`}>
                {icon("chevron")}
              </span>
            </button>
            {open ? (
              <div class="device-dropdown-list" role="listbox" onKeyDown={handleListKeyDown} onMouseDown={suppressFocusSteal}>
                {devices.map((device) => {
                  const key = device.id || device.name;
                  const isSelected =
                    device.id === selectedKey || device.name === selectedKey;
                  return (
                    <button
                      key={key}
                      type="button"
                      role="option"
                      aria-selected={isSelected}
                      class={`device-dropdown-option ${isSelected ? "is-selected" : ""}`}
                      onClick={() => {
                        onSelect(device);
                        close(true);
                      }}
                    >
                      <span class="device-dropdown-option-name">{device.name}</span>
                      {device.is_active ? (
                        <span class="device-dropdown-option-meta">
                          {localized(language, "active", "aktiv")}
                        </span>
                      ) : null}
                      {isSelected ? icon("check", "icon device-dropdown-check") : null}
                    </button>
                  );
                })}
              </div>
            ) : null}
          </div>
          <button
            type="button"
            class="icon-button"
            aria-label={localized(language, "Refresh speakers", "Lautsprecher aktualisieren")}
            onClick={onRefresh}
          >
            {icon("refresh")}
          </button>
        </div>
      )}
    </div>
  );
}

interface LibraryPickerProps {
  enabled: boolean;
  offline: boolean;
  collections: Record<LibrarySection, CollectionState>;
  artistDrilldown: ArtistDrilldown;
  selectedUri: string;
  currentSection: LibrarySection;
  onSectionChange: (section: LibrarySection) => void;
  onEnsureSection: (section: LibrarySection) => void;
  onRetrySection: (section: LibrarySection) => void;
  onSelect: (item: LibraryItem) => void;
  onQuickPlay?: (item: LibraryItem) => void;
  quickPlayBusy?: boolean;
  quickPlayDisabled?: boolean;
  onOpenArtist: (artist: LibraryItem) => void;
  onSearchCatalog: (query: string, force?: boolean) => void;
  onCloseArtist: () => void;
  recents?: RecentEntry[];
  t: TranslateFn;
  language: string;
}

const LIBRARY_PAGE_SIZE = 30;

function LibraryPicker({
  enabled,
  offline,
  collections,
  artistDrilldown,
  selectedUri,
  currentSection,
  onSectionChange,
  onEnsureSection,
  onRetrySection,
  onSelect,
  onQuickPlay,
  quickPlayBusy = false,
  quickPlayDisabled = false,
  onOpenArtist,
  onSearchCatalog,
  onCloseArtist,
  recents = [],
  t,
  language
}: LibraryPickerProps) {
  const [query, setQuery] = useState("");
  const [visibleCount, setVisibleCount] = useState(LIBRARY_PAGE_SIZE);
  const searchDebounceRef = useRef<number | null>(null);
  const lastSearchRequestRef = useRef<string | null>(null);
  const sentinelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setQuery("");
  }, [currentSection, artistDrilldown.artist?.uri]);

  useEffect(() => {
    setVisibleCount(LIBRARY_PAGE_SIZE);
  }, [currentSection, query, artistDrilldown.artist?.uri]);

  useEffect(() => {
    if (currentSection !== "search") {
      if (searchDebounceRef.current) {
        window.clearTimeout(searchDebounceRef.current);
      }
      lastSearchRequestRef.current = null;
      return;
    }

    const normalizedQuery = query.trim();
    if (lastSearchRequestRef.current === normalizedQuery) {
      return;
    }

    if (searchDebounceRef.current) {
      window.clearTimeout(searchDebounceRef.current);
    }
    searchDebounceRef.current = window.setTimeout(() => {
      lastSearchRequestRef.current = normalizedQuery;
      void onSearchCatalog(query);
    }, 300);

    return () => {
      if (searchDebounceRef.current) {
        window.clearTimeout(searchDebounceRef.current);
      }
    };
  }, [currentSection, onSearchCatalog, query]);

  if (!enabled) {
    return (
      <div class="state-card state-card-muted">
        <p>
          {localized(
            language,
            "Music browsing is disabled in Settings.",
            "Musik-Browsing ist in den Einstellungen deaktiviert."
          )}
        </p>
      </div>
    );
  }

  const currentCollection =
    currentSection === "artists" && artistDrilldown.artist
      ? {
          status: artistDrilldown.status,
          items: artistDrilldown.items,
          errorMessage: artistDrilldown.errorMessage
        }
      : collections[currentSection];

  const filteredItems =
    currentSection === "search"
      ? currentCollection.items
      : currentCollection.items.filter((item) => {
          const haystack = `${item.name} ${item.artist || ""}`.toLowerCase();
          return haystack.includes(query.toLowerCase());
        });

  const visibleItems =
    currentSection === "search" ? filteredItems : filteredItems.slice(0, visibleCount);
  const hasMoreItems = currentSection !== "search" && visibleCount < filteredItems.length;

  const currentSectionIndex = Math.max(0, LIBRARY_SECTIONS.indexOf(currentSection));
  const resultRegionId = `library-results-${currentSection}`;
  const showOfflineState = offline || currentCollection.status === "offline";
  const searchGroupOrder: Array<{ type: string; label: string }> = [
    { type: "track", label: localized(language, "Tracks", "Titel") },
    { type: "album", label: localized(language, "Albums", "Alben") },
    { type: "artist", label: localized(language, "Artists", "Künstler") },
    { type: "playlist", label: localized(language, "Playlists", "Playlists") }
  ];

  const groupedSearchItems = searchGroupOrder
    .map((group) => ({
      ...group,
      items: filteredItems.filter((item) => item.type === group.type)
    }))
    .filter((group) => group.items.length > 0);

  useEffect(() => {
    if (!hasMoreItems) {
      return;
    }
    const sentinel = sentinelRef.current;
    if (!sentinel) {
      return;
    }

    let scrollableParent: HTMLElement | null = sentinel.parentElement;
    while (scrollableParent && scrollableParent !== document.body) {
      const overflowY = window.getComputedStyle(scrollableParent).overflowY;
      if (overflowY === "auto" || overflowY === "scroll") {
        break;
      }
      scrollableParent = scrollableParent.parentElement;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) {
          setVisibleCount((current) =>
            Math.min(current + LIBRARY_PAGE_SIZE, filteredItems.length)
          );
        }
      },
      {
        root: scrollableParent ?? null,
        rootMargin: "300px"
      }
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [hasMoreItems, filteredItems.length]);

  function activateSection(section: LibrarySection) {
    onSectionChange(section);
    if (section !== "search") {
      onEnsureSection(section);
    }
  }

  function handleTabKeyNavigation(
    event: JSX.TargetedKeyboardEvent<HTMLButtonElement>,
    index: number
  ) {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
      return;
    }
    event.preventDefault();

    let nextIndex = index;
    if (event.key === "ArrowRight") {
      nextIndex = (index + 1) % LIBRARY_SECTIONS.length;
    } else if (event.key === "ArrowLeft") {
      nextIndex = (index - 1 + LIBRARY_SECTIONS.length) % LIBRARY_SECTIONS.length;
    } else if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = LIBRARY_SECTIONS.length - 1;
    }

    const nextSection = LIBRARY_SECTIONS[nextIndex];
    activateSection(nextSection);
    document.getElementById(`library-tab-${nextSection}`)?.focus();
  }

  function renderLibraryRow(item: LibraryItem, keySuffix = "") {
    const selected = selectedUri === item.uri;
    const isArtistRoot = currentSection === "artists" && !artistDrilldown.artist;
    const showActionButton = Boolean(onQuickPlay);
    const showInlineArtistArrow = isArtistRoot && !showActionButton;
    const safeImageUrl = normalizeImageUrl(item.image_url);
    const meta = item.artist
      ? item.artist
      : item.track_count
        ? localized(language, `${item.track_count} tracks`, `${item.track_count} Titel`)
        : item.type || "";

    return (
      <div key={`${item.uri}-${item.type || "item"}-${keySuffix}`} class={`library-row ${selected ? "is-selected" : ""}`}>
        <button
          type="button"
          class="library-row-main"
          aria-pressed={selected}
          onClick={() => (isArtistRoot ? onOpenArtist(item) : onSelect(item))}
        >
          {safeImageUrl ? (
            <img
              class="library-artwork"
              src={safeImageUrl}
              alt=""
              loading="lazy"
              referrerPolicy="no-referrer"
            />
          ) : (
            <span class="artwork-fallback artwork-fallback-compact" aria-hidden="true">
              {icon("library")}
            </span>
          )}
          <span class="library-row-copy">
            <span class="library-row-title">{item.name}</span>
            <span class="library-row-meta">{meta}</span>
          </span>
          {selected && !isArtistRoot ? icon("check", "icon icon-check") : null}
          {showInlineArtistArrow ? icon("arrow", "icon icon-muted") : null}
        </button>
        {showActionButton ? (
          <button
            type="button"
            class="library-row-action"
            disabled={isArtistRoot ? false : quickPlayDisabled || quickPlayBusy}
            data-testid="library-quick-play"
            aria-label={
              isArtistRoot
                ? localized(language, "Open artist", "Künstler öffnen")
                : localized(language, "Play now", "Jetzt abspielen")
            }
            onClick={() => {
              if (isArtistRoot) {
                onOpenArtist(item);
                return;
              }
              onQuickPlay?.(item);
            }}
          >
            {isArtistRoot ? icon("arrow") : icon("play")}
          </button>
        ) : null}
      </div>
    );
  }

  return (
    <div class="field-group">
      <label class="field-label">
        {localized(language, "Choose music", "Musik auswählen")}
      </label>
      <div class="library-picker" data-testid="library-picker">
        {recents.length > 0 && currentSection !== "search" && !artistDrilldown.artist ? (
          <section class="library-recents" data-testid="library-recents" aria-label={localized(language, "Recently picked", "Zuletzt gewählt")}>
            <span class="library-recents-heading">
              {localized(language, "Recently picked", "Zuletzt gewählt")}
            </span>
            <div class="library-recents-strip">
              {recents.map((entry) => {
                const safeImageUrl = normalizeImageUrl(entry.image_url);
                const isSelected = selectedUri === entry.uri;
                return (
                  <button
                    key={`recent-${entry.uri}`}
                    type="button"
                    class={`library-recent-chip ${isSelected ? "is-selected" : ""}`}
                    aria-pressed={isSelected}
                    onClick={() => onSelect({
                      uri: entry.uri,
                      name: entry.name,
                      image_url: entry.image_url,
                      type: entry.type
                    })}
                  >
                    {safeImageUrl ? (
                      <img
                        class="library-artwork"
                        src={safeImageUrl}
                        alt=""
                        loading="lazy"
                        referrerPolicy="no-referrer"
                      />
                    ) : (
                      <span class="artwork-fallback artwork-fallback-compact" aria-hidden="true">
                        {icon("library")}
                      </span>
                    )}
                    <span class="library-recent-chip-copy">
                      <span class="library-recent-chip-title">{entry.name}</span>
                      <span class="library-recent-chip-meta">{entry.meta}</span>
                    </span>
                  </button>
                );
              })}
            </div>
          </section>
        ) : null}
        <div class="section-tabs" role="tablist" aria-label={localized(language, "Music sections", "Musikbereiche")}>
          {LIBRARY_SECTIONS.map((section) => {
            const active = section === currentSection;
            return (
              <button
                key={section}
                id={`library-tab-${section}`}
                type="button"
                role="tab"
                class={`section-tab ${active ? "is-active" : ""}`}
                aria-selected={active}
                aria-controls={resultRegionId}
                tabIndex={active ? 0 : -1}
                onClick={() => activateSection(section)}
                onKeyDown={(event) => handleTabKeyNavigation(event, LIBRARY_SECTIONS.indexOf(section))}
              >
                {section === "playlists"
                  ? t("playlists", localized(language, "Playlists", "Playlists"))
                  : section === "albums"
                    ? t("albums", localized(language, "Albums", "Alben"))
                    : section === "tracks"
                      ? t("songs", localized(language, "Songs", "Songs"))
                      : section === "artists"
                        ? t("artists", localized(language, "Artists", "Künstler"))
                        : section === "recent"
                          ? localized(language, "Recent", "Zuletzt")
                          : section === "top"
                            ? localized(language, "Top", "Top")
                            : localized(language, "Search", "Suche")}
              </button>
            );
          })}
        </div>

        {currentSection === "artists" && artistDrilldown.artist ? (
          <button type="button" class="ghost-button" onClick={onCloseArtist}>
            {icon("arrow")}
            <span>{localized(language, "Back to artists", "Zurück zu Künstlern")}</span>
          </button>
        ) : null}

        <input
          type="search"
          class="field-input"
          aria-label={localized(language, "Search music", "Musik suchen")}
          placeholder={t(
            currentSection === "search" ? "search_music" : "playlist_search_placeholder",
            currentSection === "search"
              ? localized(language, "Search Spotify catalog...", "Spotify-Katalog durchsuchen...")
              : localized(language, "Search music...", "Musik suchen...")
          )}
          value={query}
          onInput={(event) => setQuery((event.currentTarget as HTMLInputElement).value)}
        />

        {currentSection === "search" && query.trim().length > 0 && query.trim().length < 3 ? (
          <div class="state-card state-card-muted">
            <p>{localized(language, "Type at least 3 characters.", "Mindestens 3 Zeichen eingeben.")}</p>
          </div>
        ) : null}

        {showOfflineState ? (
          <div class="state-card state-card-muted">
            <p>{localized(language, "Offline mode. Music list is unavailable.", "Offline-Modus. Musikliste ist nicht verfügbar.")}</p>
            <button
              type="button"
              class="ghost-button"
              onClick={() => {
                if (currentSection === "search") {
                  onSearchCatalog(query, true);
                  return;
                }
                onRetrySection(currentSection);
              }}
            >
              {icon("refresh")}
              <span>{localized(language, "Retry", "Erneut versuchen")}</span>
            </button>
          </div>
        ) : null}

        {!showOfflineState && (currentCollection.status === "loading" || currentCollection.status === "pending") ? (
          <div class="state-card state-card-muted">
            <p>{t("loading_music", localized(language, "Loading music...", "Musik wird geladen..."))}</p>
          </div>
        ) : null}

        {!showOfflineState && currentCollection.status === "auth_required" ? (
          <div class="state-card state-card-danger">
            <p>{t("status_auth_required", localized(language, "Spotify sign-in required", "Spotify-Anmeldung erforderlich"))}</p>
          </div>
        ) : null}

        {!showOfflineState && currentCollection.status === "error" ? (
          <div class="state-card state-card-danger">
            <p>{currentCollection.errorMessage || t("spotify_unavailable", localized(language, "Spotify unavailable", "Spotify nicht verfügbar"))}</p>
          </div>
        ) : null}

        {!showOfflineState && (currentCollection.status === "ready" || currentCollection.status === "empty") ? (
          filteredItems.length === 0 ? (
            <div class="state-card state-card-muted">
              <p>{t("playlist_no_results", localized(language, "No music found", "Keine Musik gefunden"))}</p>
            </div>
          ) : (
            <div
              id={resultRegionId}
              class="library-list"
              role="tabpanel"
              aria-labelledby={`library-tab-${LIBRARY_SECTIONS[currentSectionIndex]}`}
            >
              {currentSection === "search"
                ? groupedSearchItems.map((group) => (
                    <Fragment key={group.type}>
                      <p class="library-search-heading">{group.label}</p>
                      {group.items.map((item) => renderLibraryRow(item, group.type))}
                    </Fragment>
                  ))
                : visibleItems.map((item) => renderLibraryRow(item))}
              {hasMoreItems ? (
                <div
                  ref={sentinelRef}
                  class="library-list-sentinel"
                  aria-hidden="true"
                />
              ) : null}
            </div>
          )
        ) : null}
      </div>
    </div>
  );
}


export function App({ bootstrap }: { bootstrap: AppBootstrap }) {
  const t = useMemo(
    () => createTranslator(bootstrap.language, bootstrap.translations),
    [bootstrap.language, bootstrap.translations]
  );
  const [settings, setSettings] = useState<SettingsData>(bootstrap.settings);
  const [surface, setSurface] = useState<SurfaceName>(
    bootstrap.app.initial_surface === "settings" ? "settings" : "none"
  );
  const [clock, setClock] = useState<Date>(() => new Date(bootstrap.app.now_iso));
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [alarmForm, setAlarmForm] = useState<AlarmFormState>(
    createAlarmFormModel(bootstrap.dashboard, bootstrap.settings)
  );
  const [sleepForm, setSleepForm] = useState<SleepFormState>(
    createSleepFormModel(bootstrap.dashboard, bootstrap.sleep_defaults)
  );
  const [playForm, setPlayForm] = useState<PlayFormState>(() => createPlayFormModel(bootstrap.dashboard));
  const [account, setAccount] = useState<AccountLoadState>({ status: "idle" });
  const [spotifySettings, setSpotifySettings] = useState<SpotifySettingsPayload | null>(null);
  const [spotifyForm, setSpotifyForm] = useState<SpotifyCredentialFormState>(createEmptySpotifyCredentialForm);
  const [spotifyTouched, setSpotifyTouched] = useState<SpotifyCredentialTouchedState>(createEmptySpotifyCredentialTouched);
  const [spotifySettingsStatus, setSpotifySettingsStatus] = useState<AsyncStatus>("idle");
  const [showClientId, setShowClientId] = useState<boolean>(false);
  const [showClientSecret, setShowClientSecret] = useState<boolean>(false);
  const [showRefreshToken, setShowRefreshToken] = useState<boolean>(false);
  const [playbackQueue, setPlaybackQueue] = useState<LibraryItem[]>([]);
  const [queueStatus, setQueueStatus] = useState<AsyncStatus>("idle");
  const { toasts, pushToast, dismissToast } = useToasts({
    initialNotifications: bootstrap.notifications
  });
  const { networkStatus, setNetworkStatus } = useNetworkStatus("online");
  const { dashboard, setDashboard, refreshDashboard } = useDashboardPolling({
    initialDashboard: bootstrap.dashboard,
    lowPower: bootstrap.low_power,
    language: bootstrap.language,
    t,
    setNetworkStatus,
    pushToast
  });
  const {
    librarySection,
    setLibrarySection,
    collections,
    artistDrilldown,
    ensureLibrarySection,
    openArtistAlbums,
    searchCatalog,
    resetArtistDrilldown
  } = useLibraryData({
    enabled: settings.feature_flags.music_library,
    surface,
    language: bootstrap.language,
    t
  });
  const handleOpenArtist = useCallback((artist: LibraryItem) => {
    void openArtistAlbums(artist);
  }, [openArtistAlbums]);
  const handleCatalogSearch = useCallback((query: string, force = false) => {
    void searchCatalog(query, force);
  }, [searchCatalog]);
  const { oledTheme, setOledTheme } = useTheme();
  const alarmRecents = useLibraryRecents("alarm");
  const sleepRecents = useLibraryRecents("sleep");
  const playRecents = useLibraryRecents("play");
  const pendingRecentRef = useRef<{ alarm: LibraryItem | null; sleep: LibraryItem | null; play: LibraryItem | null }>({
    alarm: null,
    sleep: null,
    play: null
  });
  const {
    playerVolume,
    isPlayerReady: playerReady,
    handleVolumeInput,
    handlePlaybackCommand
  } = usePlaybackActions({
    dashboard,
    settings,
    networkStatus,
    language: bootstrap.language,
    t,
    setBusyAction,
    pushToast,
    refreshDashboard,
    setDashboard
  });
  const { updateSetting, handleClearCache } = useSettingsMutations({
    language: bootstrap.language,
    t,
    setSettings,
    setBusyAction,
    pushToast,
    refreshDashboard
  });

  const loadSpotifySettings = useCallback(async (notifyOnError = false) => {
    setSpotifySettingsStatus("loading");
    setAccount({ status: "loading" });

    const maxAttempts = 2;
    for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
      const controller = new AbortController();
      const timeoutId = window.setTimeout(() => controller.abort(), 5000);

      try {
        const result = await getJson<SpotifySettingsPayload>("/api/settings/spotify", {
          signal: controller.signal
        });
        window.clearTimeout(timeoutId);

        if (!result.body?.success || !result.body.data) {
          const message =
            result.body?.message ||
            localized(bootstrap.language, "Unable to load Spotify settings", "Spotify-Einstellungen konnten nicht geladen werden");
          throw new Error(message);
        }

        const payload = result.body.data;
        setSpotifySettings(payload);
        setSpotifyForm({
          clientId: "",
          clientSecret: "",
          refreshToken: "",
          username: payload.credentials.username.value || ""
        });
        setSpotifyTouched(createEmptySpotifyCredentialTouched());
        setShowClientId(false);
        setShowClientSecret(false);
        setShowRefreshToken(false);
        setAccount(toAccountLoadState(payload, bootstrap.language));
        setSpotifySettingsStatus("ready");
        return;
      } catch (error) {
        window.clearTimeout(timeoutId);

        if (attempt < maxAttempts) {
          await new Promise<void>((resolve) => {
            window.setTimeout(resolve, 350);
          });
          continue;
        }

        const fallback = localized(
          bootstrap.language,
          "Unable to load Spotify settings",
          "Spotify-Einstellungen konnten nicht geladen werden"
        );
        const message = error instanceof Error ? error.message : fallback;
        const lowerMessage = message.toLowerCase();
        const isOffline = lowerMessage.includes("network") || lowerMessage.includes("abort");

        setSpotifySettingsStatus(isOffline ? "offline" : "error");
        setAccount({
          status: isOffline ? "offline" : "error",
          errorMessage: message || fallback
        });
        if (notifyOnError) {
          pushToast("error", message || fallback);
        }
      }
    }
  }, [bootstrap.language, pushToast]);

  // The clock only renders HH:MM (plus a date), so tick once per minute aligned to
  // the next minute boundary instead of re-rendering the entire app every second.
  // On a Pi Zero W browser / low-end phone this drops ~59 wasted full reconciliations
  // per minute. Self-correcting: each tick reschedules off the real clock.
  useEffect(() => {
    let timeoutId = 0;
    const tick = () => {
      const now = new Date();
      setClock(now);
      const msUntilNextMinute = 60000 - (now.getSeconds() * 1000 + now.getMilliseconds());
      timeoutId = window.setTimeout(tick, msUntilNextMinute);
    };
    tick();
    return () => window.clearTimeout(timeoutId);
  }, []);

  useEffect(() => {
    if (surface !== "settings") {
      return;
    }
    void loadSpotifySettings(false);
  }, [surface, loadSpotifySettings]);

  useEffect(() => {
    if (surface === "none") {
      return;
    }

    const { body, documentElement } = document;
    const previousBodyOverflow = body.style.overflow;
    const previousBodyPaddingRight = body.style.paddingRight;
    const previousHtmlOverflow = documentElement.style.overflow;
    const scrollbarWidth = window.innerWidth - documentElement.clientWidth;

    body.style.overflow = "hidden";
    documentElement.style.overflow = "hidden";
    if (scrollbarWidth > 0) {
      body.style.paddingRight = `${scrollbarWidth}px`;
    }

    return () => {
      body.style.overflow = previousBodyOverflow;
      body.style.paddingRight = previousBodyPaddingRight;
      documentElement.style.overflow = previousHtmlOverflow;
    };
  }, [surface]);

  useEffect(() => {
    if (surface === "sleep" && !settings.feature_flags.sleep_timer && !dashboard.sleep.active) {
      setSurface("none");
    }
  }, [dashboard.sleep.active, settings.feature_flags.sleep_timer, surface]);

  useEffect(() => {
    if (dashboard.devices.length === 0) {
      return;
    }

    const deviceStillAvailable = playForm.deviceId
      ? dashboard.devices.some((device) => device.id === playForm.deviceId)
      : false;
    if (deviceStillAvailable) {
      return;
    }

    const rememberedDeviceId = readLastDeviceId();
    const rememberedDevice = rememberedDeviceId
      ? dashboard.devices.find((device) => device.id === rememberedDeviceId)
      : undefined;
    const preferredDevice = rememberedDevice || getPreferredDevice(dashboard.devices);

    if (preferredDevice?.id) {
      setPlayForm((current) => ({
        ...current,
        deviceId: preferredDevice.id || "",
        deviceName: preferredDevice.name || ""
      }));
    }
  }, [dashboard.devices, playForm.deviceId]);

  useEffect(() => {
    let cancelled = false;
    let intervalId = 0;

    async function loadQueue() {
      // Don't proxy to Spotify while the tab/PWA is backgrounded — mirrors the
      // visibility-aware behaviour of useDashboardPolling so a hidden app stops
      // hitting the Pi every 15s (saves CPU/network on the Pi and mobile battery).
      if (typeof document !== "undefined" && document.hidden) {
        return;
      }
      if (networkStatus === "offline") {
        setQueueStatus("offline");
        setPlaybackQueue([]);
        return;
      }
      if (dashboard.playback_status === "auth_required") {
        setQueueStatus("auth_required");
        setPlaybackQueue([]);
        return;
      }

      setQueueStatus((current) => (current === "ready" ? current : "loading"));
      try {
        const result = await getJson<PlaybackQueuePayload>("/api/playback/queue");
        if (cancelled) {
          return;
        }
        if (result.status === 401 || result.body?.error_code === "auth_required") {
          setQueueStatus("auth_required");
          setPlaybackQueue([]);
          return;
        }
        if (result.body?.error_code === "insufficient_scope") {
          setQueueStatus("error");
          setPlaybackQueue([]);
          return;
        }
        if (result.body?.success && result.body.data) {
          const queueItems = (result.body.data.queue || []).slice(0, 5);
          setPlaybackQueue(queueItems);
          setQueueStatus(queueItems.length ? "ready" : "empty");
          return;
        }
        setQueueStatus("error");
        setPlaybackQueue([]);
      } catch {
        if (!cancelled) {
          setQueueStatus("offline");
          setPlaybackQueue([]);
        }
      }
    }

    function handleVisibility() {
      // Refresh immediately when the app is brought back to the foreground.
      if (!document.hidden) {
        void loadQueue();
      }
    }

    void loadQueue();
    intervalId = window.setInterval(() => {
      void loadQueue();
    }, 15000);
    document.addEventListener("visibilitychange", handleVisibility);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [dashboard.playback.current_track?.name, dashboard.playback_status, networkStatus]);

  function openSurface(nextSurface: SurfaceName) {
    if (nextSurface === "sleep" && !settings.feature_flags.sleep_timer && !dashboard.sleep.active) {
      return;
    }
    if (nextSurface === "play" && !settings.feature_flags.music_library) {
      return;
    }

    if (nextSurface === "alarm") {
      setAlarmForm(createAlarmFormModel(dashboard, settings));
    } else if (nextSurface === "sleep") {
      setSleepForm(createSleepFormModel(dashboard, bootstrap.sleep_defaults));
    } else if (nextSurface === "play") {
      setPlayForm((current) => ({
        ...createPlayFormModel(dashboard),
        contextUri: current.contextUri
      }));
    }

    setSurface(nextSurface);
  }

  function closeSurface() {
    setSurface("none");
  }

  function handleLibrarySelect(item: LibraryItem) {
    if (surface === "alarm") {
      pendingRecentRef.current.alarm = item;
      setAlarmForm((current) => {
        const next = { ...current, playlistUri: item.uri, playlistName: item.name || "" };
        void handleAlarmSave(next);
        return next;
      });
    } else if (surface === "sleep") {
      pendingRecentRef.current.sleep = item;
      setSleepForm((current) => ({ ...current, playlistUri: item.uri }));
    } else if (surface === "play") {
      pendingRecentRef.current.play = item;
      setPlayForm((current) => ({ ...current, contextUri: item.uri }));
    }
  }

  function handleSpotifyFieldChange(field: keyof SpotifyCredentialFormState, value: string) {
    setSpotifyForm((current) => ({
      ...current,
      [field]: value
    }));
    setSpotifyTouched((current) => ({
      ...current,
      [field]: true
    }));
  }

  async function handleSpotifySettingsSave() {
    const payload: Record<string, string> = {};
    if (spotifyTouched.clientId) {
      payload.client_id = spotifyForm.clientId.trim();
    }
    if (spotifyTouched.clientSecret) {
      payload.client_secret = spotifyForm.clientSecret.trim();
    }
    if (spotifyTouched.refreshToken) {
      payload.refresh_token = spotifyForm.refreshToken.trim();
    }
    if (spotifyTouched.username) {
      payload.username = spotifyForm.username.trim();
    }

    if (Object.keys(payload).length === 0) {
      pushToast(
        "info",
        localized(
          bootstrap.language,
          "No Spotify credential changes to save.",
          "Keine Spotify-Zugangsdaten geändert."
        )
      );
      return;
    }

    setBusyAction("spotify-save");
    try {
      const result = await patchJson<{ spotify?: SpotifySettingsPayload; updated?: string[] }>(
        "/api/settings/spotify",
        { credentials: payload }
      );

      if (!result.body?.success) {
        pushToast(
          "error",
          result.body?.message ||
            localized(
              bootstrap.language,
              "Spotify settings could not be saved.",
              "Spotify-Einstellungen konnten nicht gespeichert werden."
            )
        );
        return;
      }

      if (result.body.data?.spotify) {
        const nextSpotifySettings = result.body.data.spotify;
        setSpotifySettings(nextSpotifySettings);
        setSpotifyForm({
          clientId: "",
          clientSecret: "",
          refreshToken: "",
          username: nextSpotifySettings.credentials.username.value || ""
        });
        setSpotifyTouched(createEmptySpotifyCredentialTouched());
        setShowClientId(false);
        setShowClientSecret(false);
        setShowRefreshToken(false);
        setAccount(toAccountLoadState(nextSpotifySettings, bootstrap.language));
      } else {
        await loadSpotifySettings(false);
      }
    } catch (error) {
      pushToast(
        "error",
        error instanceof Error
          ? error.message
          : localized(
              bootstrap.language,
              "Spotify settings could not be saved.",
              "Spotify-Einstellungen konnten nicht gespeichert werden."
            )
      );
    } finally {
      setBusyAction(null);
    }
  }

  function handleSpotifyConnect() {
    if (spotifyTouched.clientId || spotifyTouched.clientSecret) {
      pushToast(
        "info",
        localized(
          bootstrap.language,
          "Save Client ID and Client Secret before starting OAuth.",
          "Bitte zuerst Client ID und Client Secret speichern."
        )
      );
      return;
    }

    const startUrl = spotifySettings?.oauth.start_url || "/api/settings/spotify/oauth/start";
    window.location.assign(startUrl);
  }

  async function handleSpotifyDisconnect() {
    setBusyAction("spotify-disconnect");
    try {
      const result = await postForm<{ spotify?: SpotifySettingsPayload }>(
        "/api/settings/spotify/disconnect",
        new URLSearchParams()
      );
      if (!result.body?.success) {
        pushToast(
          "error",
          result.body?.message ||
            localized(bootstrap.language, "Spotify disconnect failed.", "Spotify-Trennung fehlgeschlagen.")
        );
        return;
      }

      if (result.body.data?.spotify) {
        const nextSpotifySettings = result.body.data.spotify;
        setSpotifySettings(nextSpotifySettings);
        setSpotifyForm({
          clientId: "",
          clientSecret: "",
          refreshToken: "",
          username: nextSpotifySettings.credentials.username.value || ""
        });
        setSpotifyTouched(createEmptySpotifyCredentialTouched());
        setShowClientId(false);
        setShowClientSecret(false);
        setShowRefreshToken(false);
        setAccount(toAccountLoadState(nextSpotifySettings, bootstrap.language));
      } else {
        await loadSpotifySettings(false);
      }
    } catch (error) {
      pushToast(
        "error",
        error instanceof Error
          ? error.message
          : localized(bootstrap.language, "Spotify disconnect failed.", "Spotify-Trennung fehlgeschlagen.")
      );
    } finally {
      setBusyAction(null);
    }
  }

  async function handleSpotifyReload() {
    setBusyAction("spotify-reload");
    try {
      await loadSpotifySettings(true);
    } finally {
      setBusyAction(null);
    }
  }

  async function handleAlarmSave(form?: AlarmFormState, intent?: "activate") {
    const data = form ?? alarmForm;
    const isActivating = intent === "activate";

    if (networkStatus === "offline") {
      if (isActivating) {
        pushToast(
          "error",
          localized(
            bootstrap.language,
            "You are offline. Reconnect before saving the alarm.",
            "Du bist offline. Bitte vor dem Speichern des Weckers wieder verbinden."
          )
        );
      }
      return;
    }

    if (!isValidTimeInput(data.time)) {
      if (isActivating) {
        pushToast(
          "error",
          localized(
            bootstrap.language,
            "Enter a valid alarm time (HH:MM).",
            "Bitte eine gültige Weckzeit eingeben (HH:MM)."
          )
        );
      }
      return;
    }

    if (!data.deviceName.trim()) {
      if (isActivating) {
        pushToast(
          "error",
          localized(
            bootstrap.language,
            "Choose a speaker for the alarm.",
            "Bitte einen Lautsprecher für den Wecker auswählen."
          )
        );
      }
      return;
    }

    if (isActivating && data.enabled && data.weekdays.length === 0 && hasPassedToday(data.time)) {
      pushToast(
        "info",
        localized(
          bootstrap.language,
          "That time has already passed today. The alarm will ring tomorrow.",
          "Diese Uhrzeit ist heute bereits vorbei. Der Wecker klingelt morgen."
        )
      );
    }

    setBusyAction("alarm");

    const payload = new URLSearchParams();
    payload.set("enabled", data.enabled ? "on" : "off");
    payload.set("time", data.time);
    payload.set("device_name", data.deviceName);
    payload.set("playlist_uri", data.playlistUri);
    payload.set("playlist_name", data.playlistName);
    payload.set("alarm_volume", String(data.alarmVolume));
    payload.set("volume", String(playerVolume));
    payload.set("fade_in", data.fadeIn ? "on" : "off");
    payload.set("shuffle", data.shuffle ? "on" : "off");
    payload.set("snooze_enabled", data.snoozeEnabled ? "on" : "off");
    payload.set("weekdays", JSON.stringify(data.weekdays));

    try {
      const result = await postForm<AlarmSummary>("/save_alarm", payload);
      if (result.body?.success && result.body.data) {
        setDashboard((current) => ({
          ...current,
          alarm: { ...current.alarm, ...result.body!.data! }
        }));
        const pendingItem = pendingRecentRef.current.alarm;
        if (pendingItem && pendingItem.uri === data.playlistUri) {
          alarmRecents.recordRecent(pendingItem, buildRecentMeta(pendingItem, bootstrap.language));
        }
        return;
      }

      pushToast(
        "error",
        result.body?.message ||
          t("save_failed", localized(bootstrap.language, "Save failed", "Speichern fehlgeschlagen"))
      );
    } catch (error) {
      pushToast(
        "error",
        error instanceof Error ? error.message : localized(bootstrap.language, "Save failed", "Speichern fehlgeschlagen")
      );
    } finally {
      setBusyAction(null);
    }
  }

  // Inline dashboard toggle: build a fresh form from the latest dashboard state so we
  // never persist a stale alarmForm, then reuse the same save path as the alarm sheet.
  function handleAlarmEnabledToggle(enabled: boolean) {
    const next = { ...createAlarmFormModel(dashboard, settings), enabled };
    setAlarmForm(next);
    void handleAlarmSave(next, enabled ? "activate" : undefined);
  }

  async function handleSleepStart() {
    if (networkStatus === "offline") {
      pushToast(
        "error",
        localized(
          bootstrap.language,
          "You are offline. Reconnect before starting sleep timer.",
          "Du bist offline. Bitte vor dem Start des Sleep-Timers wieder verbinden."
        )
      );
      return;
    }

    if (!sleepForm.deviceName.trim()) {
      pushToast(
        "error",
        localized(
          bootstrap.language,
          "Choose a speaker before starting the timer.",
          "Bitte zuerst einen Lautsprecher auswählen."
        )
      );
      return;
    }

    if (sleepForm.duration === "custom") {
      const customDuration = Number(sleepForm.customDuration);
      if (!Number.isFinite(customDuration) || customDuration < 1 || customDuration > 480) {
        pushToast(
          "error",
          localized(
            bootstrap.language,
            "Custom duration must be between 1 and 480 minutes.",
            "Die benutzerdefinierte Dauer muss zwischen 1 und 480 Minuten liegen."
          )
        );
        return;
      }
    }

    setBusyAction("sleep");

    const payload = new URLSearchParams();
    payload.set("duration", sleepForm.duration);
    if (sleepForm.duration === "custom") {
      payload.set("custom_duration", sleepForm.customDuration);
    }
    payload.set("device_name", sleepForm.deviceName);
    payload.set("playlist_uri", sleepForm.playlistUri);
    payload.set("sleep_volume", String(sleepForm.volume));
    payload.set("shuffle", sleepForm.shuffle ? "on" : "off");

    try {
      const result = await postForm<Record<string, unknown>>("/sleep", payload);
      if (result.body?.success) {
        const pendingItem = pendingRecentRef.current.sleep;
        if (pendingItem && pendingItem.uri === sleepForm.playlistUri) {
          sleepRecents.recordRecent(pendingItem, buildRecentMeta(pendingItem, bootstrap.language));
        }
        await refreshDashboard(true);
        closeSurface();
        return;
      }

      pushToast(
        "error",
        result.body?.message ||
          t("failed_start_sleep", localized(bootstrap.language, "Failed to start sleep", "Sleep konnte nicht gestartet werden"))
      );
    } catch (error) {
      pushToast(
        "error",
        error instanceof Error ? error.message : localized(bootstrap.language, "Sleep failed", "Sleep fehlgeschlagen")
      );
    } finally {
      setBusyAction(null);
    }
  }

  async function handleSleepStop() {
    setBusyAction("sleep-stop");
    try {
      const result = await postForm<Record<string, unknown>>("/stop_sleep", new URLSearchParams());
      if (result.body?.success) {
        await refreshDashboard(true);
        closeSurface();
        return;
      }
      pushToast(
        "error",
        result.body?.message ||
          t("failed_stop_sleep", localized(bootstrap.language, "Failed to stop sleep", "Sleep-Timer konnte nicht gestoppt werden"))
      );
    } catch (error) {
      pushToast(
        "error",
        error instanceof Error ? error.message : localized(bootstrap.language, "Sleep failed", "Sleep fehlgeschlagen")
      );
    } finally {
      setBusyAction(null);
    }
  }

  async function handleSnoozeStop() {
    setBusyAction("snooze-stop");
    try {
      const result = await postForm<Record<string, unknown>>("/api/snooze/stop", new URLSearchParams());
      if (result.body?.success) {
        await refreshDashboard(true);
        return;
      }
      pushToast(
        "error",
        result.body?.message ||
          localized(bootstrap.language, "Failed to stop snooze", "Snooze konnte nicht gestoppt werden")
      );
    } catch (error) {
      pushToast(
        "error",
        error instanceof Error ? error.message : localized(bootstrap.language, "Snooze failed", "Snooze fehlgeschlagen")
      );
    } finally {
      setBusyAction(null);
    }
  }

  async function handlePlayNow(contextUriOverride?: string) {
    const contextUri = contextUriOverride || playForm.contextUri;
    const deviceId = playForm.deviceId;

    if (networkStatus === "offline") {
      pushToast(
        "error",
        localized(
          bootstrap.language,
          "You are offline. Reconnect before starting playback.",
          "Du bist offline. Bitte vor dem Start der Wiedergabe wieder verbinden."
        )
      );
      return;
    }

    if (!contextUri || !deviceId) {
      pushToast(
        "error",
        localized(
          bootstrap.language,
          "Choose both a speaker and music first.",
          "Bitte zuerst Lautsprecher und Musik auswählen."
        )
      );
      return;
    }

    if (contextUriOverride && contextUriOverride !== playForm.contextUri) {
      setPlayForm((current) => ({ ...current, contextUri: contextUriOverride }));
    }

    setBusyAction("play");
    try {
      const result = await postJson<Record<string, unknown>>("/play", {
        context_uri: contextUri,
        device_id: deviceId
      });
      if (result.body?.success) {
        writeLastDeviceId(deviceId);
        const pendingItem = pendingRecentRef.current.play;
        if (pendingItem && pendingItem.uri === contextUri) {
          playRecents.recordRecent(pendingItem, buildRecentMeta(pendingItem, bootstrap.language));
        }
        await refreshDashboard(true);
        closeSurface();
        return;
      }

      pushToast(
        "error",
        result.body?.message ||
          t("failed_start_playback", localized(bootstrap.language, "Failed to start playback", "Wiedergabe konnte nicht gestartet werden"))
      );
    } catch (error) {
      pushToast(
        "error",
        error instanceof Error ? error.message : localized(bootstrap.language, "Playback failed", "Wiedergabe fehlgeschlagen")
      );
    } finally {
      setBusyAction(null);
    }
  }

  const isPlaybackInitialHydration =
    dashboard.playback_status === "pending" ||
    (dashboard.hydration.playback.pending && !dashboard.hydration.playback.has_data);

  const statusSnapshot = useMemo(() => {
    if (networkStatus === "offline") {
      return {
        label: t("status_offline", localized(bootstrap.language, "Offline mode", "Offline-Modus")),
        tone: "warning" as const
      };
    }
    if (isPlaybackInitialHydration && !dashboard.playback.current_track) {
      return {
        label: t("status_pending", localized(bootstrap.language, "Waiting for Spotify", "Warte auf Spotify")),
        tone: "muted" as const
      };
    }
    if (dashboard.playback_status === "auth_required") {
      return {
        label: t("status_auth_required", localized(bootstrap.language, "Spotify sign-in required", "Spotify-Anmeldung erforderlich")),
        tone: "danger" as const
      };
    }
    if (dashboard.playback_status === "error") {
      return {
        label: t("spotify_error", localized(bootstrap.language, "Spotify error", "Spotify-Fehler")),
        tone: "danger" as const
      };
    }
    if (dashboard.playback.current_track) {
      return {
        label: dashboard.playback.is_playing
          ? t("currently_playing", localized(bootstrap.language, "Currently playing", "Aktuell läuft"))
          : t("paused", localized(bootstrap.language, "Paused", "Pausiert")),
        tone: "success" as const
      };
    }
    return {
      label: t("no_active_playback", localized(bootstrap.language, "No active playback", "Keine aktive Wiedergabe")),
      tone: "muted" as const
    };
  }, [dashboard, isPlaybackInitialHydration, networkStatus, t, bootstrap.language]);

  const clockLabel = formatDateTime(clock, bootstrap.language);
  const currentTrack = dashboard.playback.current_track;
  const safeCurrentTrackImage = normalizeImageUrl(currentTrack?.album_image);
  const safeAccountAvatar = normalizeImageUrl(account.profile?.avatar_url);
  const storedClientId = spotifySettings?.credentials.client_id.value || "";
  const maskedClientId = spotifySettings?.credentials.client_id.masked || "";
  const maskedClientSecret = spotifySettings?.credentials.client_secret.masked || "";
  const maskedRefreshToken = spotifySettings?.credentials.refresh_token.masked || "";
  const hasSpotifyClientId = Boolean(spotifySettings?.credentials.client_id.set);
  const hasSpotifyClientSecret = Boolean(spotifySettings?.credentials.client_secret.set);
  const hasSpotifyRefreshToken = Boolean(spotifySettings?.credentials.refresh_token.set);
  const spotifySaveDisabled = busyAction === "spotify-save";
  const spotifyReloadDisabled = busyAction === "spotify-reload";
  const spotifyDisconnectDisabled = busyAction === "spotify-disconnect";
  const sleepSurfaceVisible = settings.feature_flags.sleep_timer;
  const hiddenCredentialPreview = "************";
  const clientIdStoredPreview = showClientId ? storedClientId || maskedClientId : hiddenCredentialPreview;
  const clientSecretStoredPreview = showClientSecret ? maskedClientSecret : hiddenCredentialPreview;
  const refreshTokenStoredPreview = showRefreshToken ? maskedRefreshToken : hiddenCredentialPreview;
  const canToggleClientIdVisibility = hasSpotifyClientId || Boolean(spotifyForm.clientId);
  const canToggleClientSecretVisibility = hasSpotifyClientSecret || Boolean(spotifyForm.clientSecret);
  const canToggleRefreshTokenVisibility = hasSpotifyRefreshToken || Boolean(spotifyForm.refreshToken);
  const dismissToastLabel = localized(bootstrap.language, "Dismiss notification", "Hinweis schließen");
  const closeSheetLabel = localized(bootstrap.language, "Close panel", "Panel schließen");
  const playerTitle = currentTrack?.name
    ? currentTrack.name
    : networkStatus === "offline"
      ? localized(bootstrap.language, "Offline, still usable", "Offline, aber bedienbar")
      : dashboard.playback_status === "auth_required"
        ? localized(bootstrap.language, "Spotify sign-in needed", "Spotify-Anmeldung nötig")
        : dashboard.playback_status === "error"
          ? localized(bootstrap.language, "Spotify is unavailable", "Spotify ist gerade nicht erreichbar")
          : isPlaybackInitialHydration
            ? localized(bootstrap.language, "Spotify is waking up", "Spotify wacht auf")
            : localized(bootstrap.language, "Ready to play", "Bereit zum Abspielen");
  const playerSubtitle = currentTrack?.artist || "";
  const playerHasPlaceholderCopy = !currentTrack?.name;
  const primaryFlows: PrimaryFlowSnapshot = toPrimaryFlowSnapshot(dashboard);
  const devicesStatus = dashboard.devices_meta.status || "pending";
  // Older configs only stored the playlist URI; fall back to the recents cache for name + cover.
  const alarmRecentEntry = primaryFlows.alarmPlaylistUri
    ? alarmRecents.recents.find((entry) => entry.uri === primaryFlows.alarmPlaylistUri)
    : undefined;
  const alarmMusicName = primaryFlows.alarmPlaylistName || alarmRecentEntry?.name || "";
  const alarmMusicImage = normalizeImageUrl(alarmRecentEntry?.image_url);
  const alarmTimeLabel = formatTimeLabel(primaryFlows.alarmTime, bootstrap.language);
  const alarmRepeatLabel = primaryFlows.alarmEnabled
    ? formatWeekdaySummary(primaryFlows.alarmWeekdays, bootstrap.language)
    : localized(bootstrap.language, "Off", "Aus");
  const alarmDeviceLabel =
    primaryFlows.alarmDeviceName || localized(bootstrap.language, "No speaker", "Kein Lautsprecher");
  const alarmMusicLabel = alarmMusicName || localized(bootstrap.language, "No music", "Keine Musik");
  const alarmCardAriaLabel = primaryFlows.alarmEnabled
    ? localized(
        bootstrap.language,
        `Alarm at ${alarmTimeLabel}, ${alarmDeviceLabel}, ${alarmMusicLabel}. Open alarm settings.`,
        `Wecker um ${alarmTimeLabel}, ${alarmDeviceLabel}, ${alarmMusicLabel}. Wecker-Einstellungen öffnen.`
      )
    : localized(bootstrap.language, "No alarm scheduled. Open alarm settings.", "Kein Wecker geplant. Wecker-Einstellungen öffnen.");
  const sleepSummary = primaryFlows.sleepActive
    ? formatCountdown(primaryFlows.sleepRemainingSeconds, bootstrap.language)
    : localized(bootstrap.language, "No sleep timer running", "Kein Sleep-Timer aktiv");
  const playSummary = primaryFlows.availableDevices
    ? localized(
        bootstrap.language,
        `${primaryFlows.availableDevices} speaker${primaryFlows.availableDevices === 1 ? "" : "s"} ready`,
        `${primaryFlows.availableDevices} Lautsprecher bereit`
      )
    : localized(bootstrap.language, "Speaker list will hydrate here", "Lautsprecherliste lädt hier");
  return (
    <Fragment>
      <div class="app-shell">
        <ToastStack
          items={toasts}
          dismissLabel={dismissToastLabel}
          onDismiss={dismissToast}
        />

        <header class="app-header">
          <div class="brand">
            <span class="brand-mark" aria-hidden="true">
              {icon("spotify")}
            </span>
            <div>
              <h1>SpotiPi</h1>
            </div>
          </div>

          <div class="header-meta">
            <StatusPill tone={statusSnapshot.tone} label={statusSnapshot.label} />
            <div class="clock-panel">
              <span class="clock-time">{clockLabel.time}</span>
              <span class="clock-separator" aria-hidden="true">·</span>
              <span class="clock-date">{clockLabel.date}</span>
            </div>
            <button
              type="button"
              class="icon-button icon-button-strong"
              aria-label={localized(bootstrap.language, "Open settings", "Einstellungen öffnen")}
              onClick={() => openSurface("settings")}
              data-testid="settings-trigger"
            >
              {icon("settings")}
            </button>
          </div>
        </header>

        {networkStatus === "offline" ? (
          <section class="banner banner-warning">
            <strong>{localized(bootstrap.language, "Offline", "Offline")}</strong>
          </section>
        ) : null}

        {dashboard.sleep.active ? (
          <section class="banner banner-success" data-testid="sleep-active-banner">
            <strong>{localized(bootstrap.language, "Sleep timer running", "Sleep-Timer läuft")}</strong>
            <span>{formatCountdown(dashboard.sleep.remaining_seconds, bootstrap.language)}</span>
            <div class="banner-actions">
              <button
                type="button"
                class="ghost-button"
                onClick={() => openSurface("sleep")}
              >
                {localized(bootstrap.language, "Manage", "Verwalten")}
              </button>
              <button
                type="button"
                class="ghost-button"
                disabled={busyAction === "sleep-stop"}
                onClick={() => void handleSleepStop()}
              >
                {busyAction === "sleep-stop"
                  ? localized(bootstrap.language, "Stopping...", "Stoppt...")
                  : localized(bootstrap.language, "Stop", "Stoppen")}
              </button>
            </div>
          </section>
        ) : null}

        {dashboard.snooze?.active ? (
          <section class="banner banner-success" data-testid="snooze-active-banner">
            <strong>
              {dashboard.snooze.state === "snoozing"
                ? localized(bootstrap.language, "Snoozing", "Snooze läuft")
                : localized(bootstrap.language, "Snooze armed", "Snooze scharf")}
            </strong>
            <span>
              {dashboard.snooze.state === "snoozing"
                ? localized(
                    bootstrap.language,
                    `Alarm resumes in ${formatCountdown(dashboard.snooze.resume_in_seconds, bootstrap.language)}`,
                    `Wecker startet in ${formatCountdown(dashboard.snooze.resume_in_seconds, bootstrap.language)}`
                  )
                : localized(bootstrap.language, "Pause to snooze", "Pause = Snooze")}
            </span>
            <div class="banner-actions">
              <button
                type="button"
                class="ghost-button"
                disabled={busyAction === "snooze-stop"}
                onClick={() => void handleSnoozeStop()}
              >
                {busyAction === "snooze-stop"
                  ? localized(bootstrap.language, "Stopping...", "Stoppt...")
                  : localized(bootstrap.language, "Dismiss", "Beenden")}
              </button>
            </div>
          </section>
        ) : null}

        <main class="dashboard-grid">
          <section class="player-card" data-testid="player-card">
            <div class="player-artwork">
              {safeCurrentTrackImage ? (
                <img src={safeCurrentTrackImage} alt="" loading="eager" fetchpriority="high" referrerPolicy="no-referrer" />
              ) : (
                <div
                  class="artwork-fallback player-artwork-fallback"
                  role="img"
                  aria-label={localized(bootstrap.language, "No active Spotify playback", "Keine aktive Spotify-Wiedergabe")}
                  data-testid="player-fallback-artwork"
                >
                  <span class="player-artwork-ring" aria-hidden="true">
                    <span class="player-artwork-ring-core" />
                  </span>
                  <div class="player-artwork-fallback-copy">
                    <strong>{localized(bootstrap.language, "DJ off duty", "DJ macht Pause")}</strong>
                    <span>{localized(bootstrap.language, "Spotify is not active yet.", "Spotify ist gerade noch nicht aktiv.")}</span>
                  </div>
                  <div class="player-artwork-fallback-bars" aria-hidden="true">
                    <span />
                    <span />
                    <span />
                  </div>
                </div>
              )}
            </div>

            <div class="player-copy">
              <div class="player-heading">
                <StatusPill tone={statusSnapshot.tone} label={statusSnapshot.label} />
              </div>

              <h2 class={playerHasPlaceholderCopy ? "player-title-placeholder" : undefined}>{playerTitle}</h2>
              {playerSubtitle ? <p>{playerSubtitle}</p> : null}

              <div class="player-controls" role="group" aria-label={t("playback_controls", localized(bootstrap.language, "Playback controls", "Wiedergabe-Steuerung"))}>
                <button
                  type="button"
                  class="control-button"
                  disabled={!playerReady || busyAction === "previous"}
                  onClick={() => void handlePlaybackCommand("/api/playback/previous", "previous")}
                  aria-label={t("previous_track", localized(bootstrap.language, "Previous track", "Vorheriger Titel"))}
                >
                  {icon("previous")}
                </button>
                <button
                  type="button"
                  class="control-button control-button-primary"
                  disabled={!playerReady || busyAction === "toggle"}
                  onClick={() => void handlePlaybackCommand("/toggle_play_pause", "toggle")}
                  aria-label={t("play_pause", localized(bootstrap.language, "Play or pause", "Play/Pause"))}
                >
                  {icon(dashboard.playback.is_playing ? "pause" : "play")}
                </button>
                <button
                  type="button"
                  class="control-button"
                  disabled={!playerReady || busyAction === "next"}
                  onClick={() => void handlePlaybackCommand("/api/playback/next", "next")}
                  aria-label={t("next_track", localized(bootstrap.language, "Next track", "Nächster Titel"))}
                >
                  {icon("next")}
                </button>
              </div>

              <div class="player-queue" data-testid="player-queue">
                <div class="field-label-row">
                  <label class="field-label">{localized(bootstrap.language, "Up next", "Als Nächstes")}</label>
                </div>
                {queueStatus === "loading" ? (
                  <p class="player-queue-placeholder">{localized(bootstrap.language, "Loading queue...", "Lade Warteschlange...")}</p>
                ) : null}
                {queueStatus === "auth_required" ? (
                  <p class="player-queue-placeholder">{localized(bootstrap.language, "Spotify sign-in required", "Spotify-Anmeldung erforderlich")}</p>
                ) : null}
                {queueStatus === "error" ? (
                  <p class="player-queue-placeholder">{localized(bootstrap.language, "Queue unavailable", "Warteschlange nicht verfügbar")}</p>
                ) : null}
                {queueStatus === "empty" ? (
                  <p class="player-queue-placeholder">{localized(bootstrap.language, "Queue is empty", "Warteschlange ist leer")}</p>
                ) : null}
                {queueStatus === "ready" ? (
                  <ul class="player-queue-list">
                    {playbackQueue.map((item) => (
                      <li key={`${item.uri}-${item.type || "item"}`} class="player-queue-item">
                        <span class="player-queue-title">{item.name}</span>
                        <span class="player-queue-meta">{item.artist || item.type || ""}</span>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>

              <div class="range-field">
                <div class="field-label-row">
                  <label class="field-label">{t("volume_label", localized(bootstrap.language, "Volume", "Lautstärke"))}</label>
                  <strong>{playerVolume}%</strong>
                </div>
                <input
                  type="range"
                  min="0"
                  max="100"
                  value={playerVolume}
                  aria-label={t("volume_label", localized(bootstrap.language, "Volume", "Lautstärke"))}
                  onInput={(event) => handleVolumeInput(Number((event.currentTarget as HTMLInputElement).value))}
                />
              </div>
            </div>
          </section>

          <section class="snapshot-card">
            <div class="snapshot-grid">
              <div
                class={`alarm-hero ${primaryFlows.alarmEnabled ? "" : "is-off"}`}
                data-testid="alarm-card"
              >
                <button
                  type="button"
                  class="alarm-hero-open"
                  onClick={() => openSurface("alarm")}
                  aria-label={alarmCardAriaLabel}
                >
                  <span class="alarm-hero-head">
                    <span class="alarm-hero-mark">{icon("alarm")}</span>
                    <small>{localized(bootstrap.language, "Alarm", "Wecker")}</small>
                  </span>
                  <span class="alarm-hero-time">
                    <strong>{alarmTimeLabel}</strong>
                    <span class="alarm-hero-repeat">{alarmRepeatLabel}</span>
                  </span>
                  <span class="alarm-hero-meta">
                    <span class="alarm-hero-row">
                      <span class="alarm-hero-rowicon">{icon("device")}</span>
                      <span class="alarm-hero-rowtext">{alarmDeviceLabel}</span>
                    </span>
                    <span class="alarm-hero-row">
                      <span class="alarm-hero-rowicon">{icon("volume")}</span>
                      <span class="alarm-hero-rowtext">{primaryFlows.alarmVolume}%</span>
                    </span>
                    <span class="alarm-hero-row">
                      {alarmMusicImage ? (
                        <img
                          class="alarm-hero-cover"
                          src={alarmMusicImage}
                          alt=""
                          loading="lazy"
                          referrerPolicy="no-referrer"
                        />
                      ) : (
                        <span class="alarm-hero-rowicon">{icon("note")}</span>
                      )}
                      <span class="alarm-hero-rowtext">{alarmMusicLabel}</span>
                    </span>
                  </span>
                </button>
                <label class="toggle-control alarm-hero-toggle" data-testid="alarm-enable-toggle">
                  <input
                    type="checkbox"
                    checked={primaryFlows.alarmEnabled}
                    disabled={busyAction === "alarm" || networkStatus === "offline"}
                    aria-label={localized(bootstrap.language, "Enable alarm", "Wecker aktivieren")}
                    onChange={(event) =>
                      handleAlarmEnabledToggle((event.currentTarget as HTMLInputElement).checked)
                    }
                  />
                  <span class="toggle-track" aria-hidden="true" />
                </label>
              </div>
              {sleepSurfaceVisible ? (
                <button
                  type="button"
                  class="snapshot-item snapshot-item-action"
                  onClick={() => openSurface("sleep")}
                  data-testid="sleep-card"
                >
                  <span>{icon("sleep")}</span>
                  <div>
                    <small>{localized(bootstrap.language, "Sleep", "Sleep")}</small>
                    <strong>{sleepSummary}</strong>
                  </div>
                  <span class="snapshot-item-caret">{icon("chevron")}</span>
                </button>
              ) : null}
              <button
                type="button"
                class={`snapshot-item snapshot-item-action ${!settings.feature_flags.music_library ? "is-disabled" : ""}`}
                disabled={!settings.feature_flags.music_library}
                onClick={() => openSurface("play")}
                data-testid="play-card"
              >
                <span>{icon("play")}</span>
                <div>
                  <small>{localized(bootstrap.language, "Play now", "Jetzt abspielen")}</small>
                  <strong>{playSummary}</strong>
                </div>
                <span class="snapshot-item-caret">{icon("chevron")}</span>
              </button>
            </div>

          </section>
        </main>

        <footer class="app-footer">
          <span>{bootstrap.app.info}</span>
        </footer>
      </div>

      <Sheet
        id="alarm-sheet"
        open={surface === "alarm"}
        onClose={closeSurface}
        closeLabel={closeSheetLabel}
        title={localized(bootstrap.language, "Set alarm", "Wecker stellen")}
      >
        <div class="sheet-stack" data-testid="alarm-sheet">
          <div class="field-group">
            <label class="field-label" for="alarm-time-input">
              {t("alarm_time_label", localized(bootstrap.language, "Alarm time", "Weckzeit"))}
            </label>
            <input
              id="alarm-time-input"
              data-sheet-initial-focus="true"
              class="field-input field-input-large"
              type="time"
              value={alarmForm.time}
              onInput={(event) =>
                setAlarmForm((current) => ({
                  ...current,
                  time: (event.currentTarget as HTMLInputElement).value
                }))
              }
              onBlur={() => void handleAlarmSave()}
            />
          </div>

          <ToggleField
            label={t("enable_alarm", localized(bootstrap.language, "Enable alarm", "Wecker aktivieren"))}
            description={
              alarmForm.enabled
                ? localized(
                    bootstrap.language,
                    `Alarm active for ${formatTimeLabel(alarmForm.time, bootstrap.language)}.`,
                    `Wecker aktiv für ${formatTimeLabel(alarmForm.time, bootstrap.language)}.`
                  )
                : localized(
                    bootstrap.language,
                    "Alarm is currently disabled.",
                    "Der Wecker ist aktuell deaktiviert."
                  )
            }
            checked={alarmForm.enabled}
            onChange={(checked) => {
              const next = { ...alarmForm, enabled: checked };
              setAlarmForm(next);
              void handleAlarmSave(next, checked ? "activate" : undefined);
            }}
          />

          <WeekdayPicker
            value={alarmForm.weekdays}
            language={bootstrap.language}
            onChange={(weekdays) => {
              const next = { ...alarmForm, weekdays };
              setAlarmForm(next);
              void handleAlarmSave(next);
            }}
          />

          <DevicePicker
            title={t("device_label", localized(bootstrap.language, "Speaker", "Lautsprecher"))}
            devices={dashboard.devices}
            selectedKey={alarmForm.deviceName}
            status={devicesStatus}
            offline={networkStatus === "offline"}
            onSelect={(device) => {
              const next = { ...alarmForm, deviceName: device.name };
              setAlarmForm(next);
              void handleAlarmSave(next);
            }}
            onRefresh={() => void refreshDashboard(true)}
            t={t}
            language={bootstrap.language}
          />

          <div class="range-field">
            <div class="field-label-row">
              <label class="field-label">
                {t("alarm_volume", localized(bootstrap.language, "Alarm volume", "Wecker-Lautstärke"))}
              </label>
              <strong>{alarmForm.alarmVolume}%</strong>
            </div>
            <input
              type="range"
              min="0"
              max="100"
              value={alarmForm.alarmVolume}
              aria-label={t("alarm_volume", localized(bootstrap.language, "Alarm volume", "Wecker-Lautstärke"))}
              onInput={(event) =>
                setAlarmForm((current) => ({
                  ...current,
                  alarmVolume: Number((event.currentTarget as HTMLInputElement).value)
                }))
              }
              // `change` fires on commit for mouse, touch AND keyboard (arrow keys),
              // so keyboard-only volume edits are persisted too (previously the save
              // hung only on onMouseUp/onTouchEnd and silently dropped keyboard edits).
              // Read the committed value straight from the event and pass it to the
              // save explicitly: on keyboard, `input` and `change` fire synchronously
              // before Preact flushes the state update, so reading alarmForm here would
              // persist the pre-keypress value. Mirrors the Settings volume slider.
              onChange={(event) => {
                const next = {
                  ...alarmForm,
                  alarmVolume: Number((event.currentTarget as HTMLInputElement).value)
                };
                setAlarmForm(next);
                void handleAlarmSave(next);
              }}
            />
          </div>

          <ToggleField
            label={t("fade_in", localized(bootstrap.language, "Fade in", "Fade-In"))}
            checked={alarmForm.fadeIn}
            onChange={(checked) => {
              const next = { ...alarmForm, fadeIn: checked };
              setAlarmForm(next);
              void handleAlarmSave(next);
            }}
          />
          <ToggleField
            label={t("shuffle", localized(bootstrap.language, "Shuffle", "Shuffle"))}
            checked={alarmForm.shuffle}
            onChange={(checked) => {
              const next = { ...alarmForm, shuffle: checked };
              setAlarmForm(next);
              void handleAlarmSave(next);
            }}
          />
          <ToggleField
            label={localized(bootstrap.language, "Snooze on pause (9 min)", "Snooze bei Pause (9 Min)")}
            checked={alarmForm.snoozeEnabled}
            onChange={(checked) => {
              const next = { ...alarmForm, snoozeEnabled: checked };
              setAlarmForm(next);
              void handleAlarmSave(next);
            }}
          />

          <LibraryPicker
            enabled={settings.feature_flags.music_library}
            offline={networkStatus === "offline"}
            collections={collections}
            artistDrilldown={artistDrilldown}
            selectedUri={alarmForm.playlistUri}
            currentSection={librarySection}
            onSectionChange={(section) => {
              setLibrarySection(section);
              resetArtistDrilldown();
            }}
            onEnsureSection={(section) => void ensureLibrarySection(section)}
            onRetrySection={(section) => void ensureLibrarySection(section)}
            onSelect={handleLibrarySelect}
            onOpenArtist={handleOpenArtist}
            onSearchCatalog={handleCatalogSearch}
            onCloseArtist={resetArtistDrilldown}
            recents={alarmRecents.recents}
            t={t}
            language={bootstrap.language}
          />

        </div>
      </Sheet>

      <Sheet
        id="sleep-sheet"
        open={surface === "sleep"}
        onClose={closeSurface}
        closeLabel={closeSheetLabel}
        title={localized(bootstrap.language, "Sleep flow", "Sleep-Flow")}
      >
        <div class="sheet-stack">
          {dashboard.sleep.active ? (
            <div class="state-card state-card-active">
              <h3>{localized(bootstrap.language, "Sleep timer is running", "Sleep-Timer läuft")}</h3>
              <p>{formatCountdown(dashboard.sleep.remaining_seconds, bootstrap.language)}</p>
              <div class="progress-bar" aria-hidden="true">
                <span style={`width:${clamp(Number(dashboard.sleep.progress_percent || 0), 0, 100)}%`} />
              </div>
              <button
                type="button"
                data-sheet-initial-focus="true"
                class="primary-button button-danger"
                disabled={busyAction === "sleep-stop"}
                onClick={() => void handleSleepStop()}
              >
                {busyAction === "sleep-stop"
                  ? localized(bootstrap.language, "Stopping...", "Stoppt...")
                  : t("sleep_stop", localized(bootstrap.language, "Stop sleep", "Sleep stoppen"))}
              </button>
            </div>
          ) : (
            <Fragment>
              <div class="field-group">
                <label class="field-label">
                  {t("duration_label", localized(bootstrap.language, "Duration", "Dauer"))}
                </label>
                <div class="chip-row">
                  {DURATION_OPTIONS.map((option) => {
                    const active = sleepForm.duration === String(option);
                    return (
                      <button
                        key={option}
                        type="button"
                        data-sheet-initial-focus={option === DURATION_OPTIONS[0] ? "true" : undefined}
                        class={`choice-chip ${active ? "is-active" : ""}`}
                        onClick={() =>
                          setSleepForm((current) => ({
                            ...current,
                            duration: String(option),
                            customDuration: String(option)
                          }))
                        }
                      >
                        {option}m
                      </button>
                    );
                  })}
                  <button
                    type="button"
                    class={`choice-chip ${sleepForm.duration === "custom" ? "is-active" : ""}`}
                    onClick={() =>
                      setSleepForm((current) => ({
                        ...current,
                        duration: "custom"
                      }))
                    }
                  >
                    {localized(bootstrap.language, "Custom", "Custom")}
                  </button>
                </div>
                {sleepForm.duration === "custom" ? (
                  <input
                    class="field-input"
                    type="number"
                    min="1"
                    max="480"
                    aria-label={localized(bootstrap.language, "Custom duration in minutes", "Benutzerdefinierte Dauer in Minuten")}
                    value={sleepForm.customDuration}
                    onInput={(event) =>
                      setSleepForm((current) => ({
                        ...current,
                        customDuration: (event.currentTarget as HTMLInputElement).value
                      }))
                    }
                  />
                ) : null}
              </div>

              <DevicePicker
                title={t("device_label", localized(bootstrap.language, "Speaker", "Lautsprecher"))}
                devices={dashboard.devices}
                selectedKey={sleepForm.deviceName}
                status={devicesStatus}
                offline={networkStatus === "offline"}
                onSelect={(device) =>
                  setSleepForm((current) => ({ ...current, deviceName: device.name }))
                }
                onRefresh={() => void refreshDashboard(true)}
                t={t}
                language={bootstrap.language}
              />

              <div class="range-field">
                <div class="field-label-row">
                  <label class="field-label">
                    {localized(bootstrap.language, "Sleep volume", "Sleep-Lautstärke")}
                  </label>
                  <strong>{sleepForm.volume}%</strong>
                </div>
                <input
                  type="range"
                  min="0"
                  max="100"
                  value={sleepForm.volume}
                  aria-label={localized(bootstrap.language, "Sleep volume", "Sleep-Lautstärke")}
                  onInput={(event) =>
                    setSleepForm((current) => ({
                      ...current,
                      volume: Number((event.currentTarget as HTMLInputElement).value)
                    }))
                  }
                />
              </div>

              <ToggleField
                label={t("shuffle", localized(bootstrap.language, "Shuffle", "Shuffle"))}
                checked={sleepForm.shuffle}
                onChange={(checked) => setSleepForm((current) => ({ ...current, shuffle: checked }))}
              />

              <LibraryPicker
                enabled={settings.feature_flags.music_library}
                offline={networkStatus === "offline"}
                collections={collections}
                artistDrilldown={artistDrilldown}
                selectedUri={sleepForm.playlistUri}
                currentSection={librarySection}
                onSectionChange={(section) => {
                  setLibrarySection(section);
                  resetArtistDrilldown();
                }}
                onEnsureSection={(section) => void ensureLibrarySection(section)}
                onRetrySection={(section) => void ensureLibrarySection(section)}
                onSelect={handleLibrarySelect}
                onOpenArtist={handleOpenArtist}
                onSearchCatalog={handleCatalogSearch}
                onCloseArtist={resetArtistDrilldown}
                recents={sleepRecents.recents}
                t={t}
                language={bootstrap.language}
              />

              <div class="sheet-actions">
                <button type="button" class="secondary-button" onClick={closeSurface}>
                  {localized(bootstrap.language, "Cancel", "Abbrechen")}
                </button>
                <button
                  type="button"
                  class="primary-button"
                  disabled={busyAction === "sleep"}
                  onClick={() => void handleSleepStart()}
                >
                  {busyAction === "sleep"
                    ? localized(bootstrap.language, "Starting...", "Startet...")
                    : t("sleep_start", localized(bootstrap.language, "Start sleep", "Sleep starten"))}
                </button>
              </div>
            </Fragment>
          )}
        </div>
      </Sheet>

      <Sheet
        id="play-sheet"
        open={surface === "play"}
        onClose={closeSurface}
        closeLabel={closeSheetLabel}
        title={localized(bootstrap.language, "Play now", "Jetzt abspielen")}
      >
        <div class="sheet-stack">
          <DevicePicker
            title={t("device_label", localized(bootstrap.language, "Speaker", "Lautsprecher"))}
            devices={dashboard.devices}
            selectedKey={playForm.deviceId}
            status={devicesStatus}
            offline={networkStatus === "offline"}
            onSelect={(device) =>
              setPlayForm((current) => {
                const nextDeviceId = device.id || "";
                writeLastDeviceId(nextDeviceId);
                return {
                  ...current,
                  deviceId: nextDeviceId,
                  deviceName: device.name
                };
              })
            }
            onRefresh={() => void refreshDashboard(true)}
            t={t}
            language={bootstrap.language}
          />

          <LibraryPicker
            enabled={settings.feature_flags.music_library}
            offline={networkStatus === "offline"}
            collections={collections}
            artistDrilldown={artistDrilldown}
            selectedUri={playForm.contextUri}
            currentSection={librarySection}
            onSectionChange={(section) => {
              setLibrarySection(section);
              resetArtistDrilldown();
            }}
            onEnsureSection={(section) => void ensureLibrarySection(section)}
            onRetrySection={(section) => void ensureLibrarySection(section)}
            onSelect={handleLibrarySelect}
            onQuickPlay={(item) => {
              pendingRecentRef.current.play = item;
              void handlePlayNow(item.uri);
            }}
            quickPlayBusy={busyAction === "play"}
            quickPlayDisabled={networkStatus === "offline" || !playForm.deviceId}
            onOpenArtist={handleOpenArtist}
            onSearchCatalog={handleCatalogSearch}
            onCloseArtist={resetArtistDrilldown}
            recents={playRecents.recents}
            t={t}
            language={bootstrap.language}
          />

          <div class="sheet-actions sheet-actions-single">
            <button type="button" class="secondary-button" onClick={closeSurface}>
              {localized(bootstrap.language, "Cancel", "Abbrechen")}
            </button>
          </div>
        </div>
      </Sheet>

      <Sheet
        id="settings-sheet"
        open={surface === "settings"}
        onClose={closeSurface}
        closeLabel={closeSheetLabel}
        variant="settings"
        title={localized(bootstrap.language, "Settings", "Einstellungen")}
      >
        <div class="sheet-stack" data-testid="settings-sheet">
          <section class="settings-group settings-group-account">
            <h3>{t("spotify_account", localized(bootstrap.language, "Spotify account", "Spotify-Konto"))}</h3>
            {spotifySettingsStatus === "loading" ? (
              <div class="state-card state-card-muted">
                <p>{t("loading_account", localized(bootstrap.language, "Loading account...", "Lade Konto-Informationen..."))}</p>
              </div>
            ) : null}
            {account.status === "missing_credentials" ? (
              <div class="state-card state-card-muted">
                <p>
                  {account.errorMessage ||
                    localized(
                      bootstrap.language,
                      "Client ID and Client Secret are missing.",
                      "Client ID und Client Secret fehlen."
                    )}
                </p>
              </div>
            ) : null}
            {account.status === "auth_required" ? (
              <div class="state-card state-card-danger">
                <p>
                  {account.errorMessage ||
                    t("status_auth_required", localized(bootstrap.language, "Spotify sign-in required", "Spotify-Anmeldung erforderlich"))}
                </p>
              </div>
            ) : null}
            {account.status === "offline" || account.status === "error" ? (
              <div class="state-card state-card-danger">
                <p>{account.errorMessage || t("account_error", localized(bootstrap.language, "Error loading account", "Fehler beim Laden des Kontos"))}</p>
              </div>
            ) : null}
            {account.status === "connected" && account.profile ? (
              <div class="account-card">
                <div class="account-avatar">
                  {safeAccountAvatar ? (
                    <img src={safeAccountAvatar} alt="" loading="lazy" referrerPolicy="no-referrer" />
                  ) : (
                    icon("spotify", "icon icon-xl")
                  )}
                </div>
                <div>
                  <strong>{account.profile.display_name || "Spotify"}</strong>
                  <p>{account.profile.email || localized(bootstrap.language, "Connected account", "Verbundenes Konto")}</p>
                </div>
                {account.profile.product === "premium" ? (
                  <StatusPill tone="success" label="Premium" />
                ) : null}
              </div>
            ) : null}

            <div class="field-group">
              <label class="field-label" for="spotify-client-id">
                {localized(bootstrap.language, "Spotify Client ID", "Spotify Client ID")}
              </label>
              <div class="credential-input-shell">
                {!spotifyForm.clientId && hasSpotifyClientId ? (
                  <span
                    id="spotify-client-id-display"
                    class={`credential-display ${showClientId ? "is-revealed" : "is-masked"}`}
                    aria-live="polite"
                  >
                    {clientIdStoredPreview}
                  </span>
                ) : null}
                <input
                  id="spotify-client-id"
                  class="field-input credential-input"
                  type={showClientId ? "text" : "password"}
                  autoComplete="off"
                  placeholder={!hasSpotifyClientId ? localized(bootstrap.language, "Not set", "Nicht gesetzt") : ""}
                  value={spotifyForm.clientId}
                  onInput={(event) =>
                    handleSpotifyFieldChange("clientId", (event.currentTarget as HTMLInputElement).value)
                  }
                />
                {canToggleClientIdVisibility ? (
                  <button
                    type="button"
                    class="icon-button credential-visibility-toggle"
                    aria-label={
                      showClientId
                        ? localized(bootstrap.language, "Hide Spotify Client ID", "Spotify Client ID ausblenden")
                        : localized(bootstrap.language, "Show Spotify Client ID", "Spotify Client ID anzeigen")
                    }
                    aria-pressed={showClientId}
                    aria-controls="spotify-client-id spotify-client-id-display"
                    onClick={() => setShowClientId((current) => !current)}
                  >
                    {icon(showClientId ? "eye-off" : "eye")}
                  </button>
                ) : null}
              </div>
            </div>

            <div class="field-group">
              <label class="field-label" for="spotify-client-secret">
                {localized(bootstrap.language, "Spotify Client Secret", "Spotify Client Secret")}
              </label>
              <div class="credential-input-shell">
                {!spotifyForm.clientSecret && hasSpotifyClientSecret ? (
                  <span
                    id="spotify-client-secret-display"
                    class={`credential-display ${showClientSecret ? "is-revealed" : "is-masked"}`}
                    aria-live="polite"
                  >
                    {clientSecretStoredPreview}
                  </span>
                ) : null}
                <input
                  id="spotify-client-secret"
                  class="field-input credential-input"
                  type={showClientSecret ? "text" : "password"}
                  autoComplete="off"
                  placeholder={!hasSpotifyClientSecret ? localized(bootstrap.language, "Not set", "Nicht gesetzt") : ""}
                  value={spotifyForm.clientSecret}
                  onInput={(event) =>
                    handleSpotifyFieldChange("clientSecret", (event.currentTarget as HTMLInputElement).value)
                  }
                />
                {canToggleClientSecretVisibility ? (
                  <button
                    type="button"
                    class="icon-button credential-visibility-toggle"
                    aria-label={
                      showClientSecret
                        ? localized(bootstrap.language, "Hide Spotify Client Secret", "Spotify Client Secret ausblenden")
                        : localized(bootstrap.language, "Show Spotify Client Secret", "Spotify Client Secret anzeigen")
                    }
                    aria-pressed={showClientSecret}
                    aria-controls="spotify-client-secret spotify-client-secret-display"
                    onClick={() => setShowClientSecret((current) => !current)}
                  >
                    {icon(showClientSecret ? "eye-off" : "eye")}
                  </button>
                ) : null}
              </div>
            </div>

            <div class="field-group">
              <label class="field-label" for="spotify-refresh-token">
                {localized(bootstrap.language, "Spotify Refresh Token", "Spotify Refresh Token")}
              </label>
              <div class="credential-input-shell">
                {!spotifyForm.refreshToken && hasSpotifyRefreshToken ? (
                  <span
                    id="spotify-refresh-token-display"
                    class={`credential-display ${showRefreshToken ? "is-revealed" : "is-masked"}`}
                    aria-live="polite"
                  >
                    {refreshTokenStoredPreview}
                  </span>
                ) : null}
                <input
                  id="spotify-refresh-token"
                  class="field-input credential-input"
                  type={showRefreshToken ? "text" : "password"}
                  autoComplete="off"
                  placeholder={!hasSpotifyRefreshToken ? localized(bootstrap.language, "Optional fallback: paste token manually", "Optional: Token manuell einfügen") : ""}
                  value={spotifyForm.refreshToken}
                  onInput={(event) =>
                    handleSpotifyFieldChange("refreshToken", (event.currentTarget as HTMLInputElement).value)
                  }
                />
                {canToggleRefreshTokenVisibility ? (
                  <button
                    type="button"
                    class="icon-button credential-visibility-toggle"
                    aria-label={
                      showRefreshToken
                        ? localized(bootstrap.language, "Hide Spotify Refresh Token", "Spotify Refresh Token ausblenden")
                        : localized(bootstrap.language, "Show Spotify Refresh Token", "Spotify Refresh Token anzeigen")
                    }
                    aria-pressed={showRefreshToken}
                    aria-controls="spotify-refresh-token spotify-refresh-token-display"
                    onClick={() => setShowRefreshToken((current) => !current)}
                  >
                    {icon(showRefreshToken ? "eye-off" : "eye")}
                  </button>
                ) : null}
              </div>
            </div>

            <div class="field-group">
              <label class="field-label" for="spotify-username">
                {localized(bootstrap.language, "Spotify Username", "Spotify Benutzername")}
              </label>
              <input
                id="spotify-username"
                class="field-input"
                type="text"
                autoComplete="off"
                placeholder={localized(bootstrap.language, "Spotify username", "Spotify Benutzername")}
                value={spotifyForm.username}
                onInput={(event) =>
                  handleSpotifyFieldChange("username", (event.currentTarget as HTMLInputElement).value)
                }
              />
            </div>

            <div class="sheet-actions">
              <button
                type="button"
                class="secondary-button"
                disabled={spotifyReloadDisabled}
                onClick={() => void handleSpotifyReload()}
              >
                {spotifyReloadDisabled
                  ? localized(bootstrap.language, "Reloading...", "Lädt neu...")
                  : localized(bootstrap.language, "Reload status", "Status neu laden")}
              </button>
              <button
                type="button"
                class="secondary-button"
                onClick={handleSpotifyConnect}
              >
                {localized(bootstrap.language, "Connect Spotify", "Mit Spotify verbinden")}
              </button>
              <button
                type="button"
                class="secondary-button"
                disabled={spotifyDisconnectDisabled || !hasSpotifyRefreshToken}
                onClick={() => void handleSpotifyDisconnect()}
              >
                {spotifyDisconnectDisabled
                  ? localized(bootstrap.language, "Disconnecting...", "Trennt...")
                  : localized(bootstrap.language, "Disconnect", "Trennen")}
              </button>
              <button
                type="button"
                class="primary-button"
                disabled={spotifySaveDisabled}
                onClick={() => void handleSpotifySettingsSave()}
              >
                {spotifySaveDisabled
                  ? localized(bootstrap.language, "Saving...", "Speichert...")
                  : localized(bootstrap.language, "Save credentials", "Zugangsdaten speichern")}
              </button>
            </div>
          </section>

          <section class="settings-group settings-group-features">
            <h3>{t("feature_flags", localized(bootstrap.language, "Features", "Funktionen"))}</h3>
            <ToggleField
              label={localized(bootstrap.language, "Sleep timer surface", "Sleep-Timer Oberfläche")}
              description={localized(
                bootstrap.language,
                "Keep the dedicated sleep flow visible on the dashboard.",
                "Die dedizierte Sleep-Oberfläche im Dashboard sichtbar halten."
              )}
              checked={settings.feature_flags.sleep_timer}
              onChange={(checked) => void updateSetting("feature_flags.sleep_timer", checked)}
            />
            <ToggleField
              label={localized(bootstrap.language, "Music browsing", "Musik-Browsing")}
              description={localized(
                bootstrap.language,
                "Enable the quick music picker used by alarm, sleep and play now.",
                "Den schnellen Musik-Picker für Wecker, Sleep und Jetzt abspielen aktivieren."
              )}
              checked={settings.feature_flags.music_library}
              onChange={(checked) => void updateSetting("feature_flags.music_library", checked)}
            />
          </section>

          <section class="settings-group settings-group-preferences">
            <h3>{localized(bootstrap.language, "Preferences", "Präferenzen")}</h3>
            <div class="field-group">
              <label class="field-label" for="settings-language">
                {t("language_label", localized(bootstrap.language, "Language", "Sprache"))}
              </label>
              <CustomSelect
                id="settings-language"
                value={settings.app.language}
                options={[
                  { value: "de", label: "Deutsch" },
                  { value: "en", label: "English" }
                ]}
                onChange={(val) => void updateSetting("app.language", val)}
              />
            </div>

            <div class="range-field">
              <div class="field-label-row">
                <label class="field-label">
                  {t("default_volume_label", localized(bootstrap.language, "Default volume", "Standard-Lautstärke"))}
                </label>
                <strong>{settings.app.default_volume}%</strong>
              </div>
              <input
                type="range"
                min="0"
                max="100"
                value={settings.app.default_volume}
                aria-label={t("default_volume_label", localized(bootstrap.language, "Default volume", "Standard-Lautstärke"))}
                onInput={(event) =>
                  setSettings((current) => ({
                    ...current,
                    app: {
                      ...current.app,
                      default_volume: Number((event.currentTarget as HTMLInputElement).value)
                    }
                  }))
                }
                onChange={(event) =>
                  void updateSetting(
                    "app.default_volume",
                    Number((event.currentTarget as HTMLInputElement).value)
                  )
                }
              />
            </div>

            <ToggleField
              label={t("oled_mode_label", localized(bootstrap.language, "OLED mode", "OLED-Modus"))}
              description={localized(
                bootstrap.language,
                "Switch the palette to deeper blacks for always-on displays.",
                "Die Palette für Always-on-Displays auf tiefere Schwarztöne umstellen."
              )}
              checked={oledTheme}
              onChange={(checked) => setOledTheme(checked)}
            />
            <div class="settings-maintenance-row">
              <button
                type="button"
                class="secondary-button"
                disabled={busyAction === "clear-cache"}
                onClick={() => void handleClearCache()}
              >
                {busyAction === "clear-cache"
                  ? localized(bootstrap.language, "Clearing cache...", "Cache wird geleert...")
                  : t("clear_cache_btn", localized(bootstrap.language, "Clear cache", "Cache leeren"))}
              </button>
              <span class="settings-meta-inline">
                {settings.environment} · v{bootstrap.app.version}
              </span>
            </div>
          </section>
        </div>
      </Sheet>
    </Fragment>
  );
}

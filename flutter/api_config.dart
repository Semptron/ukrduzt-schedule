/// Shared network configuration for the UkrDUZT schedule app.
///
/// Kept in a single small library so both screens import the same constants
/// (and unit tests can import them too) without a circular dependency on
/// main.dart or schedule_screen.dart.
library api_config;

/// API base URL, overridable at build time via
///   flutter run --dart-define=API_BASE_URL=https://staging.example.com
/// Falls back to the production endpoint when not defined.
const String kApiBaseUrl = String.fromEnvironment(
  'API_BASE_URL',
  defaultValue: 'https://ukrduzt-schedule.onrender.com',
);

/// Identifies the app to the API and any fronting proxy/WAF. Sent on every
/// request so the server logs and any rate-limiter can tell client from bot.
const Map<String, String> kHttpHeaders = {
  'User-Agent': 'UkrDUZT-Schedule-App/1.0 (Flutter)',
};

/// Network timeouts are tuned for mobile, not for desktop wired links: the
/// groups/teachers lists are tiny and should not make a student wait a full
/// minute on a flaky 3G. Schedule payloads can be larger, so they get a bit
/// more headroom.
const Duration kMetadataTimeout = Duration(seconds: 15);
const Duration kScheduleTimeout = Duration(seconds: 30);

/// Which schedule source the user is browsing.
///
/// The portal is the scraped site; `manual` is a curator-uploaded file
/// (see /api/v1/admin/upload). `all` keeps both and lets the backend dedup
/// manual-over-portal for the same (date, group, pair). This is the "two
/// sections" toggle in the UI: Розділ 1 = portal, Розділ 2 = manual.
enum ScheduleSource {
  portal,
  manual,
  all,
}

/// The query-string value the backend expects for each source. `all` is sent
/// as an explicit string (rather than omitted) so the same code path handles
/// all three modes uniformly and the URL is self-describing in logs.
String sourceQueryValue(ScheduleSource s) {
  switch (s) {
    case ScheduleSource.portal:
      return 'portal';
    case ScheduleSource.manual:
      return 'manual';
    case ScheduleSource.all:
      return 'all';
  }
}

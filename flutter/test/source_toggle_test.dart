// Unit tests for the schedule-source toggle added for the "Розділ 1 портал /
// Розділ 2 офлайн" feature.
//
// These cover the pure helpers in api_config.dart (the enum + its query-string
// mapping) and the prefs-key constant the two screens share. The CI copies
// this file into ukrduzt_app/test/.

import 'package:flutter_test/flutter_test.dart';
import 'package:ukrduzt_app/api_config.dart';
import 'package:ukrduzt_app/schedule_screen.dart' show kScheduleSourcePrefsKey;

void main() {
  group('sourceQueryValue', () {
    test('maps each source to the backend query string', () {
      expect(sourceQueryValue(ScheduleSource.portal), 'portal');
      expect(sourceQueryValue(ScheduleSource.manual), 'manual');
      expect(sourceQueryValue(ScheduleSource.all), 'all');
    });

    test('is exhaustive — every enum value maps to a non-empty string', () {
      for (final s in ScheduleSource.values) {
        expect(sourceQueryValue(s), isNotEmpty);
      }
    });
  });

  group('ScheduleSource enum', () {
    test('has exactly the three documented values', () {
      expect(ScheduleSource.values, hasLength(3));
      expect(ScheduleSource.values, contains(ScheduleSource.portal));
      expect(ScheduleSource.values, contains(ScheduleSource.manual));
      expect(ScheduleSource.values, contains(ScheduleSource.all));
    });

    test('names are stable for prefs persistence', () {
      // The prefs key stores `source.name`; renaming an enum value would break
      // stored settings, so lock the names here.
      expect(ScheduleSource.portal.name, 'portal');
      expect(ScheduleSource.manual.name, 'manual');
      expect(ScheduleSource.all.name, 'all');
    });
  });

  group('prefs key', () {
    test('is versioned and non-empty', () {
      expect(kScheduleSourcePrefsKey, 'ukrduzt_schedule_source_v1');
    });
  });

  group('URL building', () {
    // A tiny sanity check that the query string the screens assemble is
    // well-formed: `&source=<value>` appended to the base schedule URL.
    test('portal source yields a parseable schedule URL', () {
      final url = Uri.parse(
        'https://example.test/api/v1/schedule?group=101-LLG-326'
        '&source=${sourceQueryValue(ScheduleSource.portal)}',
      );
      expect(url.queryParameters['source'], 'portal');
      expect(url.queryParameters['group'], '101-LLG-326');
    });

    test('manual source yields a parseable teachers URL', () {
      final url = Uri.parse(
        'https://example.test/api/v1/teachers?source=${sourceQueryValue(ScheduleSource.manual)}',
      );
      expect(url.queryParameters['source'], 'manual');
    });
  });
}

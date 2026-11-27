// Unit tests for the pure (non-widget) helpers in schedule_screen.dart.
//
// These do not require a running device or network — they exercise the JSON
// parsing, teacher-display filter and key builders that the UI relies on.
// The build-apk workflow copies this file into ukrduzt_app/test/ so it runs
// alongside the smoke test on CI.

import 'package:flutter_test/flutter_test.dart';

// The CI copies everything under flutter/ into lib/, so we import the
// schedule_screen as if it were a library. These helpers are all top-level.
import 'package:ukrduzt_app/schedule_screen.dart';

void main() {
  group('LessonModel.fromJson', () {
    test('parses a full lesson with all fields', () {
      final m = LessonModel.fromJson({
        'date': '24.11.2026',
        'group': '101-ЛЛГ-з26',
        'lesson_number': 3,
        'time_start': '12:00',
        'time_end': '13:20',
        'subject': 'Нарисна геометрія',
        'lesson_type': 'лк',
        'teacher': 'Бабенко А. О.',
        'room': '2.123',
        'meeting_url': 'https://zoom.us/j/123',
        'meeting_code': '123 456 7890',
        'meeting_password': 'pw',
      });
      expect(m.date, '24.11.2026');
      expect(m.group, '101-ЛЛГ-з26');
      expect(m.lessonNumber, 3);
      expect(m.timeStart, '12:00');
      expect(m.timeEnd, '13:20');
      expect(m.subject, 'Нарисна геометрія');
      expect(m.lessonType, 'лк');
      expect(m.teacher, 'Бабенко А. О.');
      expect(m.room, '2.123');
      expect(m.serverMeetingUrl, 'https://zoom.us/j/123');
      expect(m.serverMeetingCode, '123 456 7890');
      expect(m.serverMeetingPassword, 'pw');
    });

    test('lesson_number as string is coerced to int', () {
      final m = LessonModel.fromJson({
        'date': '24.11.2026',
        'group': '101-ЛЛГ-з26',
        'lesson_number': '5',
        'subject': 'Фізика',
      });
      expect(m.lessonNumber, 5);
      // Falls back to the bell table when time_start/end are missing.
      expect(m.timeStart, '15:10');
      expect(m.timeEnd, '16:30');
    });

    test('garbage lesson_number falls back to 1 and 00:00 bell', () {
      final m = LessonModel.fromJson({
        'date': '24.11.2026',
        'group': '101-ЛЛГ-з26',
        'lesson_number': 'not a number',
        'subject': 'Фізика',
      });
      expect(m.lessonNumber, 1);
      expect(m.timeStart, '09:00');
      expect(m.timeEnd, '10:20');
    });

    test('round-trips through toJson and fromJson', () {
      final original = LessonModel.fromJson({
        'date': '01.12.2026',
        'group': '201-ІКСТ-з25',
        'lesson_number': 2,
        'subject': 'Вища математика',
        'teacher': 'Трикоз Л. В.',
      });
      final roundTrip = LessonModel.fromJson(original.toJson());
      expect(roundTrip.date, original.date);
      expect(roundTrip.group, original.group);
      expect(roundTrip.lessonNumber, original.lessonNumber);
      expect(roundTrip.subject, original.subject);
      expect(roundTrip.teacher, original.teacher);
    });
  });

  group('displayTeacher', () {
    test('keeps plausible Ukrainian surnames with initials', () {
      expect(displayTeacher('Бабенко А. О.'), 'Бабенко А. О.');
      expect(displayTeacher('Трикоз Л. В.'), 'Трикоз Л. В.');
    });

    test('rejects room codes and URLs', () {
      expect(displayTeacher('2.123б'), isNull);
      expect(displayTeacher('https://zoom.us/j/123'), isNull);
    });

    test('rejects too-short and latin-only noise', () {
      expect(displayTeacher('аб'), isNull);
      expect(displayTeacher('abc'), isNull);
    });
  });

  group('buildMeetingKey', () {
    test('is case- and whitespace-insensitive', () {
      final a = buildMeetingKey(subject: 'Фізика', teacher: 'Бабенко А. О.');
      final b = buildMeetingKey(subject: '  фізика  ', teacher: 'бабенко а. о.');
      expect(a, b);
    });

    test('null teacher becomes empty string in the key', () {
      final k = buildMeetingKey(subject: 'Фізика', teacher: null);
      expect(k, 'фізика|');
    });
  });

  group('ZoomAccessConfig', () {
    test('link mode has data when url is non-empty', () {
      const cfg = ZoomAccessConfig(mode: 'link', url: 'https://zoom.us/j/1');
      expect(cfg.hasAnyData, isTrue);
    });

    test('credentials mode requires code or password', () {
      const empty = ZoomAccessConfig(mode: 'credentials');
      expect(empty.hasAnyData, isFalse);
      const withCode = ZoomAccessConfig(mode: 'credentials', meetingCode: '123');
      expect(withCode.hasAnyData, isTrue);
    });

    test('round-trips through JSON', () {
      const cfg = ZoomAccessConfig(
        mode: 'credentials',
        meetingCode: '123',
        password: 'pw',
      );
      final restored = ZoomAccessConfig.fromJson(cfg.toJson());
      expect(restored.mode, 'credentials');
      expect(restored.meetingCode, '123');
      expect(restored.password, 'pw');
    });
  });
}

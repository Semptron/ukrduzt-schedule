// Unit tests for the manual-upload helpers in manual_upload_screen.dart.
//
// These exercise the JSON parsing of the /api/v1/admin/uploads list entries
// and the UploadResult wrapper — the parts that don't need a real device or
// network. The CI copies this file into ukrduzt_app/test/.

import 'package:flutter_test/flutter_test.dart';
import 'package:ukrduzt_app/manual_upload_screen.dart';

void main() {
  group('ManualUploadEntry.fromJson', () {
    test('parses a full entry', () {
      final e = ManualUploadEntry.fromJson({
        'id': 7,
        'original_name': 'Розклад-2-курс.pdf',
        'sha256': 'abc123',
        'lesson_count': 42,
        'warnings_count': 3,
        'uploaded_at': '2026-11-28 09:15:00',
        'note': 'від куратора',
      });
      expect(e.id, 7);
      expect(e.originalName, 'Розклад-2-курс.pdf');
      expect(e.sha256, 'abc123');
      expect(e.lessonCount, 42);
      expect(e.warningsCount, 3);
      expect(e.uploadedAt, '2026-11-28 09:15:00');
      expect(e.note, 'від куратора');
    });

    test('tolerates missing optional fields', () {
      final e = ManualUploadEntry.fromJson({'id': 1});
      expect(e.id, 1);
      expect(e.originalName, '');
      expect(e.sha256, '');
      expect(e.lessonCount, 0);
      expect(e.warningsCount, 0);
      expect(e.uploadedAt, '');
      expect(e.note, isNull);
    });

    test('treats a null note as null, not empty', () {
      final e = ManualUploadEntry.fromJson({
        'id': 2,
        'note': null,
      });
      expect(e.note, isNull);
    });
  });

  group('UploadResult', () {
    test('ok carries counts and id', () {
      final r = UploadResult.ok(
        lessonsAdded: 42,
        warningsCount: 3,
        manualId: 7,
      );
      expect(r.ok, isTrue);
      expect(r.message, isNull);
      expect(r.lessonsAdded, 42);
      expect(r.warningsCount, 3);
      expect(r.manualId, 7);
    });

    test('error carries a message and clears counts', () {
      final r = UploadResult.error('Не вдалося розібрати PDF');
      expect(r.ok, isFalse);
      expect(r.message, 'Не вдалося розібрати PDF');
      expect(r.lessonsAdded, isNull);
      expect(r.warningsCount, isNull);
      expect(r.manualId, isNull);
    });
  });
}

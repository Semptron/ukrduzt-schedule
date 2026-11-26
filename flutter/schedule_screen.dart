import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import 'package:url_launcher/url_launcher.dart';

const String kMoodleUrl = 'https://do.kart.edu.ua/';
const String kZoomAppUrl = 'https://zoom.us/join';
const String kMeetingsPrefsKey = 'ukrduzt_zoom_configs_v1';
const String kNotesPrefsKey = 'ukrduzt_lesson_notes_v1';
const String kSelectedGroupPrefsKey = 'ukrduzt_selected_group_v1';
const String kGroupsCachePrefsKey = 'ukrduzt_groups_cache_v2';

String _scheduleCacheKey(String group) => 'ukrduzt_schedule_cache_$group';

bool _isValidGroupName(String raw) {
  final s = raw.trim();
  if (s.isEmpty || s.length > 32) return false;

  final lower = s.toLowerCase();
  const forbiddenFragments = [
    'курс',
    'факультет',
    'денна форма',
    'заочна форма',
    'навчальн',
    'розклад',
    'семестр',
    'упп',
    'економічний',
    'загальний потік',
    'потік',
  ];
  for (final frag in forbiddenFragments) {
    if (lower.contains(frag)) return false;
  }

  return RegExp(r'[0-9]').hasMatch(s) || s.contains('-');
}

List<String> _sanitizeGroups(Iterable<dynamic> rawList) {
  final seen = <String>{};
  final cleaned = <String>[];
  for (final item in rawList) {
    final g = item.toString().trim();
    if (_isValidGroupName(g) && seen.add(g)) {
      cleaned.add(g);
    }
  }
  return cleaned;
}

class BellSlot {
  final int number;
  final String start;
  final String end;

  const BellSlot({required this.number, required this.start, required this.end});
}

const List<BellSlot> kBells = [
  BellSlot(number: 1, start: '09:00', end: '10:20'),
  BellSlot(number: 2, start: '10:30', end: '11:50'),
  BellSlot(number: 3, start: '12:00', end: '13:20'),
  BellSlot(number: 4, start: '13:40', end: '15:00'),
  BellSlot(number: 5, start: '15:10', end: '16:30'),
  BellSlot(number: 6, start: '16:40', end: '18:00'),
  BellSlot(number: 7, start: '18:10', end: '19:30'),
  BellSlot(number: 8, start: '19:40', end: '21:00'),
];

class ZoomAccessConfig {
  final String mode;
  final String? url;
  final String? meetingCode;
  final String? password;

  const ZoomAccessConfig({
    required this.mode,
    this.url,
    this.meetingCode,
    this.password,
  });

  bool get hasAnyData {
    if (mode == 'credentials') {
      return (meetingCode ?? '').trim().isNotEmpty ||
          (password ?? '').trim().isNotEmpty;
    }
    return (url ?? '').trim().isNotEmpty;
  }

  Map<String, dynamic> toJson() => {
        'mode': mode,
        'url': url,
        'meeting_code': meetingCode,
        'password': password,
      };

  factory ZoomAccessConfig.fromJson(Map<String, dynamic> json) {
    return ZoomAccessConfig(
      mode: (json['mode'] as String?) ?? 'link',
      url: json['url'] as String?,
      meetingCode: json['meeting_code'] as String?,
      password: json['password'] as String?,
    );
  }
}

class LessonModel {
  final String date;
  final String group;
  final int lessonNumber;
  final String timeStart;
  final String timeEnd;
  final String subject;
  final String? lessonType;
  final String? teacher;
  final String? room;
  final String? serverMeetingUrl;
  final String? serverMeetingCode;
  final String? serverMeetingPassword;

  LessonModel({
    required this.date,
    required this.group,
    required this.lessonNumber,
    required this.timeStart,
    required this.timeEnd,
    required this.subject,
    this.lessonType,
    this.teacher,
    this.room,
    this.serverMeetingUrl,
    this.serverMeetingCode,
    this.serverMeetingPassword,
  });

  factory LessonModel.fromJson(Map<String, dynamic> json) {
    final rawNum = json['lesson_number'];
    final lessonNum = rawNum is int
        ? rawNum
        : int.tryParse(rawNum?.toString() ?? '') ?? 1;
    BellSlot? bell;
    for (final b in kBells) {
      if (b.number == lessonNum) {
        bell = b;
        break;
      }
    }

    return LessonModel(
      date: (json['date'] ?? '').toString().trim(),
      group: (json['group'] ?? '').toString().trim(),
      lessonNumber: lessonNum,
      timeStart: (json['time_start'] as String?) ?? bell?.start ?? '00:00',
      timeEnd: (json['time_end'] as String?) ?? bell?.end ?? '00:00',
      subject: (json['subject'] ?? 'Без назви').toString().trim(),
      lessonType: json['lesson_type'] as String?,
      teacher: json['teacher'] as String?,
      room: json['room'] as String?,
      serverMeetingUrl: json['meeting_url'] as String?,
      serverMeetingCode: json['meeting_code'] as String?,
      serverMeetingPassword: json['meeting_password'] as String?,
    );
  }

  Map<String, dynamic> toJson() => {
        'date': date,
        'group': group,
        'lesson_number': lessonNumber,
        'time_start': timeStart,
        'time_end': timeEnd,
        'subject': subject,
        'lesson_type': lessonType,
        'teacher': teacher,
        'room': room,
        'meeting_url': serverMeetingUrl,
        'meeting_code': serverMeetingCode,
        'meeting_password': serverMeetingPassword,
      };
}

String buildMeetingKey({required String subject, String? teacher}) {
  final normSubject = subject.trim().toLowerCase();
  final normTeacher = (teacher ?? '').trim().toLowerCase();
  return '$normSubject|$normTeacher';
}


String? displayTeacher(String? raw) {
  if (raw == null) return null;
  final t = raw.trim();
  if (t.length < 4 || t.length > 60) return null;
  if (RegExp(r'^\d+\.[\wА-Яа-яІіЇїЄєҐґ]+$').hasMatch(t)) return null;
  if (t.toLowerCase().contains('http') || t.toLowerCase().contains('zoom')) {
    return null;
  }
  if (!RegExp(r'[А-Яа-яІіЇїЄєҐґ]').hasMatch(t)) return null;
  if (!RegExp(r'^[А-ЯІЇЄҐ]').hasMatch(t)) return null;
  return t;
}

String buildNoteKey(LessonModel lesson) {
  final normSubject = lesson.subject.trim().toLowerCase();
  final normTeacher = (lesson.teacher ?? '').trim().toLowerCase();
  return '${lesson.date}|$normSubject|$normTeacher|${lesson.lessonNumber}';
}

String _formatDateDdMmYyyy(DateTime dt) {
  final d = dt.day.toString().padLeft(2, '0');
  final m = dt.month.toString().padLeft(2, '0');
  final y = dt.year.toString();
  return '$d.$m.$y';
}

DateTime? _tryParseDdMmYyyy(String raw) {
  final parts = raw.trim().split('.');
  if (parts.length != 3) return null;
  final d = int.tryParse(parts[0]);
  final m = int.tryParse(parts[1]);
  final y = int.tryParse(parts[2]);
  if (d == null || m == null || y == null) return null;
  if (m < 1 || m > 12 || d < 1 || d > 31) return null;
  return DateTime(y, m, d);
}

DateTime _dateOnly(DateTime dt) => DateTime(dt.year, dt.month, dt.day);

class ScheduleScreen extends StatefulWidget {
  final String apiBaseUrl;
  final String initialGroup;

  const ScheduleScreen({
    super.key,
    this.apiBaseUrl = 'https://ukrduzt-schedule.onrender.com',
    this.initialGroup = '101-ЛЛГ-з26',
  });

  @override
  State<ScheduleScreen> createState() => _ScheduleScreenState();
}

class _ScheduleScreenState extends State<ScheduleScreen> {
  late String _selectedGroup;
  String? _selectedDate;
  bool _showAllDates = false;
  List<String> _groups = [];
  List<LessonModel> _lessons = [];
  Map<String, ZoomAccessConfig> _zoomConfigs = {};
  Map<String, String> _lessonNotes = {};
  bool _loading = true;
  bool _offlineBanner = false;
  String? _error;
  Timer? _ticker;
  int _scheduleRequestId = 0;

  @override
  void initState() {
    super.initState();
    _selectedGroup = widget.initialGroup;
    _initScreen();
    _ticker = Timer.periodic(const Duration(seconds: 30), (_) {
      if (mounted) setState(() {});
    });
  }

  @override
  void dispose() {
    _ticker?.cancel();
    super.dispose();
  }

  Future<void> _initScreen() async {
    final prefs = await SharedPreferences.getInstance();

    final savedGroup = prefs.getString(kSelectedGroupPrefsKey);
    if (savedGroup != null &&
        savedGroup.isNotEmpty &&
        _isValidGroupName(savedGroup)) {
      _selectedGroup = savedGroup.trim();
    }

    final rawMeetings = prefs.getString(kMeetingsPrefsKey);
    if (rawMeetings != null && rawMeetings.isNotEmpty) {
      final decoded = jsonDecode(rawMeetings) as Map<String, dynamic>;
      _zoomConfigs = decoded.map(
        (k, v) => MapEntry(
          k,
          ZoomAccessConfig.fromJson(v as Map<String, dynamic>),
        ),
      );
    }

    final rawNotes = prefs.getString(kNotesPrefsKey);
    if (rawNotes != null && rawNotes.isNotEmpty) {
      final decoded = jsonDecode(rawNotes) as Map<String, dynamic>;
      _lessonNotes = decoded.map((k, v) => MapEntry(k, v.toString()));
    }

    final rawGroups = prefs.getString(kGroupsCachePrefsKey);
    if (rawGroups != null && rawGroups.isNotEmpty) {
      _groups = _sanitizeGroups(jsonDecode(rawGroups) as List<dynamic>);
    }
    if (!_groups.contains(_selectedGroup)) {
      _groups = [_selectedGroup, ..._groups];
    }

    final cachedLessons = _readCachedLessons(prefs, _selectedGroup);
    if (cachedLessons.isNotEmpty && mounted) {
      setState(() {
        _lessons = cachedLessons;
        _selectedDate ??= _pickInitialDate(cachedLessons);
        _loading = false;
      });
    } else if (mounted) {
      setState(() {
        _selectedDate ??= _formatDateDdMmYyyy(DateTime.now());
      });
    }

    await _fetchGroups(prefs);
    await _fetchSchedule(prefs, showSpinnerIfEmpty: cachedLessons.isEmpty);
  }

  String _pickInitialDate(List<LessonModel> items) {
    final today = _dateOnly(DateTime.now());
    final todayStr = _formatDateDdMmYyyy(today);
    final hasTodayLessons = items.any((l) => l.date.trim() == todayStr);
    if (hasTodayLessons) return todayStr;

    final upcomingDates = _sortedDates(items, fromTodayOnly: true)
        .where((d) => items.any((l) => l.date.trim() == d))
        .toList();
    if (upcomingDates.isNotEmpty) return upcomingDates.first;

    final allDates = _sortedDates(items, fromTodayOnly: false);
    if (allDates.isNotEmpty) return allDates.first;
    return todayStr;
  }

  List<String> _sortedDates(
    List<LessonModel> items, {
    bool fromTodayOnly = false,
  }) {
    final seen = <String>{};
    final parsedEntries = <({String raw, DateTime dt})>[];

    for (final item in items) {
      final raw = item.date.trim();
      if (raw.isEmpty || !seen.add(raw)) continue;
      final dt = _tryParseDdMmYyyy(raw);
      if (dt != null) {
        parsedEntries.add((raw: raw, dt: _dateOnly(dt)));
      }
    }

    parsedEntries.sort((a, b) => a.dt.compareTo(b.dt));

    final today = _dateOnly(DateTime.now());
    final filtered = fromTodayOnly
        ? parsedEntries.where((e) => !e.dt.isBefore(today)).toList()
        : parsedEntries;

    final result = filtered.map((e) => e.raw).toList();
    final todayStr = _formatDateDdMmYyyy(today);
    if (fromTodayOnly && !result.contains(todayStr)) {
      result.insert(0, todayStr);
    }
    return result;
  }

  List<LessonModel> _readCachedLessons(SharedPreferences prefs, String group) {
    final raw = prefs.getString(_scheduleCacheKey(group));
    if (raw == null || raw.isEmpty) return [];
    final list = jsonDecode(raw) as List<dynamic>;
    return list
        .map((e) => LessonModel.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  ZoomAccessConfig? _getZoomConfig(LessonModel lesson) {
    final key = buildMeetingKey(subject: lesson.subject, teacher: lesson.teacher);
    final localCfg = _zoomConfigs[key];
    if (localCfg != null && localCfg.hasAnyData) {
      return localCfg;
    }

    if ((lesson.serverMeetingUrl ?? '').trim().isNotEmpty) {
      return ZoomAccessConfig(
        mode: 'link',
        url: lesson.serverMeetingUrl!.trim(),
      );
    }
    if ((lesson.serverMeetingCode ?? '').trim().isNotEmpty ||
        (lesson.serverMeetingPassword ?? '').trim().isNotEmpty) {
      return ZoomAccessConfig(
        mode: 'credentials',
        meetingCode: lesson.serverMeetingCode?.trim(),
        password: lesson.serverMeetingPassword?.trim(),
      );
    }

    return null;
  }

  Future<void> _saveZoomConfig(LessonModel lesson, ZoomAccessConfig? cfg) async {
    final key = buildMeetingKey(subject: lesson.subject, teacher: lesson.teacher);
    if (mounted) {
      setState(() {
        if (cfg == null || !cfg.hasAnyData) {
          _zoomConfigs.remove(key);
        } else {
          _zoomConfigs[key] = cfg;
        }
      });
    }

    final prefs = await SharedPreferences.getInstance();
    final encoded = _zoomConfigs.map((k, v) => MapEntry(k, v.toJson()));
    await prefs.setString(kMeetingsPrefsKey, jsonEncode(encoded));
  }

  Future<void> _saveNote(LessonModel lesson, String? note) async {
    final key = buildNoteKey(lesson);
    final cleaned = (note ?? '').trim();

    if (mounted) {
      setState(() {
        if (cleaned.isEmpty) {
          _lessonNotes.remove(key);
        } else {
          _lessonNotes[key] = cleaned;
        }
      });
    }

    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(kNotesPrefsKey, jsonEncode(_lessonNotes));
  }

  Future<void> _fetchGroups(SharedPreferences prefs) async {
    try {
      final uri = Uri.parse('${widget.apiBaseUrl}/api/v1/groups');
      final resp = await http.get(uri).timeout(const Duration(seconds: 65));
      if (resp.statusCode != 200 || !mounted) return;

      final data = jsonDecode(resp.body) as Map<String, dynamic>;
      final rawList = (data['groups'] as List<dynamic>? ?? []);
      final list = _sanitizeGroups(rawList);

      if (list.isNotEmpty) {
        final nextGroup = list.contains(_selectedGroup)
            ? _selectedGroup
            : list.first;
        final groupChanged = nextGroup != _selectedGroup;

        setState(() {
          _groups = list;
          _selectedGroup = nextGroup;
        });
        await prefs.setString(kGroupsCachePrefsKey, jsonEncode(list));
        if (groupChanged) {
          await prefs.setString(kSelectedGroupPrefsKey, nextGroup);
        }
      }
    } catch (_) {}
  }

  Future<void> _fetchSchedule(
    SharedPreferences? prefs, {
    bool showSpinnerIfEmpty = true,
  }) async {
    final reqId = ++_scheduleRequestId;
    final targetGroup = _selectedGroup;
    final sp = prefs ?? await SharedPreferences.getInstance();
    final cached = _readCachedLessons(sp, targetGroup);

    if (showSpinnerIfEmpty && cached.isEmpty && mounted) {
      setState(() {
        _loading = true;
        _error = null;
      });
    }

    try {
      final uri = Uri.parse(
        '${widget.apiBaseUrl}/api/v1/schedule?group=${Uri.encodeComponent(targetGroup)}',
      );
      final resp = await http.get(uri).timeout(const Duration(seconds: 65));

      if (!mounted || reqId != _scheduleRequestId) return;

      if (resp.statusCode == 404) {
        setState(() {
          _lessons = [];
          _selectedDate = _formatDateDdMmYyyy(DateTime.now());
          _loading = false;
          _offlineBanner = false;
          _error =
              'Для групи "$targetGroup" розклад наразі відсутній на сервері (HTTP 404). Оберіть іншу групу у верхньому списку.';
        });
        return;
      }

      if (resp.statusCode != 200) {
        throw Exception('HTTP ${resp.statusCode}');
      }

      final data = jsonDecode(resp.body) as Map<String, dynamic>;
      final items = (data['items'] as List<dynamic>? ?? [])
          .map((e) => LessonModel.fromJson(e as Map<String, dynamic>))
          .toList();

      await sp.setString(
        _scheduleCacheKey(targetGroup),
        jsonEncode(items.map((e) => e.toJson()).toList()),
      );

      if (!mounted || reqId != _scheduleRequestId) return;
      final availableDates = _sortedDates(items, fromTodayOnly: false);
      setState(() {
        _lessons = items;
        _selectedDate = (_selectedDate != null &&
                availableDates.contains(_selectedDate))
            ? _selectedDate
            : _pickInitialDate(items);
        _loading = false;
        _offlineBanner = false;
        _error = items.isEmpty
            ? 'Сервер повернув порожній розклад для групи "$targetGroup".'
            : null;
      });
    } catch (e) {
      if (!mounted || reqId != _scheduleRequestId) return;
      if (cached.isNotEmpty) {
        setState(() {
          _lessons = cached;
          _selectedDate ??= _pickInitialDate(cached);
          _loading = false;
          _offlineBanner = true;
          _error = null;
        });
      } else {
        setState(() {
          _loading = false;
          _offlineBanner = true;
          _error = 'Немає зв’язку і кешу для групи $targetGroup: $e';
        });
      }
    }
  }

  Future<void> _onGroupChanged(String? newGroup) async {
    if (newGroup == null || newGroup == _selectedGroup) return;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(kSelectedGroupPrefsKey, newGroup);

    final cached = _readCachedLessons(prefs, newGroup);
    if (!mounted) return;
    setState(() {
      _selectedGroup = newGroup;
      _lessons = cached;
      _selectedDate = _pickInitialDate(cached);
      _showAllDates = false;
      _error = null;
      _loading = cached.isEmpty;
    });

    await _fetchSchedule(prefs, showSpinnerIfEmpty: cached.isEmpty);
  }

  Future<void> _openExternalUrl(String url) async {
    final normalized = url.startsWith('http://') || url.startsWith('https://')
        ? url
        : 'https://$url';
    final uri = Uri.parse(normalized);
    if (!await launchUrl(uri, mode: LaunchMode.externalApplication)) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Не вдалося відкрити: $normalized')),
      );
    }
  }

  Future<void> _launchZoom(ZoomAccessConfig cfg) async {
    if (cfg.mode == 'link' && (cfg.url ?? '').trim().isNotEmpty) {
      await _openExternalUrl(cfg.url!.trim());
      return;
    }
    final cleanCode = (cfg.meetingCode ?? '').replaceAll(RegExp(r'\s+'), '');
    if (cleanCode.isNotEmpty) {
      final pwd = (cfg.password ?? '').trim();
      final query = pwd.isNotEmpty ? '?pwd=${Uri.encodeComponent(pwd)}' : '';
      await _openExternalUrl('https://zoom.us/j/$cleanCode$query');
      return;
    }
    await _openExternalUrl(kZoomAppUrl);
  }

  Future<void> _copyText(String label, String value) async {
    await Clipboard.setData(ClipboardData(text: value));
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text('$label скопійовано: $value')),
    );
  }

  Future<void> _showZoomDialog(LessonModel lesson) async {
    final current = _getZoomConfig(lesson);
    final hasManualOverride = _zoomConfigs.containsKey(
      buildMeetingKey(subject: lesson.subject, teacher: lesson.teacher),
    );

    String mode = current?.mode ?? 'link';
    final urlController = TextEditingController(text: current?.url ?? '');
    final codeController = TextEditingController(text: current?.meetingCode ?? '');
    final passController = TextEditingController(text: current?.password ?? '');

    final result = await showDialog<ZoomAccessConfig?>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setDialogState) => AlertDialog(
          title: const Text('Налаштування Zoom'),
          content: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  lesson.subject,
                  style: const TextStyle(fontWeight: FontWeight.w600),
                ),
                if (lesson.teacher != null) ...[
                  const SizedBox(height: 2),
                  Text(lesson.teacher!, style: Theme.of(ctx).textTheme.bodySmall),
                ],
                if (!hasManualOverride && current != null) ...[
                  const SizedBox(height: 6),
                  Text(
                    'Підставлено автоматично з розкладу — можна відредагувати',
                    style: Theme.of(ctx).textTheme.bodySmall?.copyWith(
                          color: Theme.of(ctx).colorScheme.primary,
                        ),
                  ),
                ],
                const SizedBox(height: 12),
                SegmentedButton<String>(
                  segments: const [
                    ButtonSegment(
                      value: 'link',
                      label: Text('Посилання'),
                      icon: Icon(Icons.link, size: 18),
                    ),
                    ButtonSegment(
                      value: 'credentials',
                      label: Text('Код і пароль'),
                      icon: Icon(Icons.key, size: 18),
                    ),
                  ],
                  selected: {mode},
                  onSelectionChanged: (sel) {
                    setDialogState(() => mode = sel.first);
                  },
                ),
                const SizedBox(height: 12),
                if (mode == 'link')
                  TextField(
                    controller: urlController,
                    keyboardType: TextInputType.url,
                    decoration: const InputDecoration(
                      labelText: 'Посилання на Zoom',
                      hintText: 'https://zoom.us/j/...',
                      border: OutlineInputBorder(),
                    ),
                  )
                else ...[
                  TextField(
                    controller: codeController,
                    keyboardType: TextInputType.number,
                    decoration: const InputDecoration(
                      labelText: 'Код конференції (ID)',
                      hintText: '123 456 7890',
                      border: OutlineInputBorder(),
                    ),
                  ),
                  const SizedBox(height: 10),
                  TextField(
                    controller: passController,
                    decoration: const InputDecoration(
                      labelText: 'Пароль конференції',
                      hintText: 'Наприклад: 123456',
                      border: OutlineInputBorder(),
                    ),
                  ),
                ],
                const SizedBox(height: 8),
                TextButton.icon(
                  onPressed: () => _openExternalUrl(kMoodleUrl),
                  icon: const Icon(Icons.open_in_new, size: 18),
                  label: const Text('Відкрити Moodle (do.kart.edu.ua)'),
                ),
              ],
            ),
          ),
          actions: [
            if (hasManualOverride)
              TextButton(
                onPressed: () => Navigator.pop(
                  ctx,
                  const ZoomAccessConfig(mode: 'link', url: ''),
                ),
                child: const Text('Скинути ручні зміни'),
              ),
            TextButton(
              onPressed: () => Navigator.pop(ctx, null),
              child: const Text('Скасувати'),
            ),
            FilledButton(
              onPressed: () {
                Navigator.pop(
                  ctx,
                  ZoomAccessConfig(
                    mode: mode,
                    url: urlController.text.trim(),
                    meetingCode: codeController.text.trim(),
                    password: passController.text.trim(),
                  ),
                );
              },
              child: const Text('Зберегти'),
            ),
          ],
        ),
      ),
    );

    WidgetsBinding.instance.addPostFrameCallback((_) {
      urlController.dispose();
      codeController.dispose();
      passController.dispose();
    });

    if (result != null && mounted) {
      await _saveZoomConfig(lesson, result);
    }
  }

  Future<void> _showNoteDialog(LessonModel lesson) async {
    final existing = _lessonNotes[buildNoteKey(lesson)] ?? '';
    final controller = TextEditingController(text: existing);

    final result = await showDialog<String?>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Нотатка до пари'),
        content: TextField(
          controller: controller,
          maxLines: 3,
          decoration: const InputDecoration(
            hintText: 'Контрольна, завдання в Moodle, курсова...',
            border: OutlineInputBorder(),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, null),
            child: const Text('Скасувати'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, controller.text),
            child: const Text('Зберегти'),
          ),
        ],
      ),
    );

    WidgetsBinding.instance.addPostFrameCallback((_) {
      controller.dispose();
    });

    if (result != null && mounted) await _saveNote(lesson, result);
  }

  DateTime? _parseLessonDateTime(String dateDdMmYyyy, String hhmm) {
    final baseDate = _tryParseDdMmYyyy(dateDdMmYyyy);
    if (baseDate == null) return null;
    final parts = hhmm.split(':');
    return DateTime(
      baseDate.year,
      baseDate.month,
      baseDate.day,
      int.tryParse(parts.isNotEmpty ? parts[0] : '0') ?? 0,
      int.tryParse(parts.length > 1 ? parts[1] : '0') ?? 0,
    );
  }

  double _lessonProgress(LessonModel lesson) {
    final now = DateTime.now();
    final start = _parseLessonDateTime(lesson.date, lesson.timeStart);
    final end = _parseLessonDateTime(lesson.date, lesson.timeEnd);
    if (start == null || end == null) return 0;
    final total = end.difference(start).inSeconds;
    if (total <= 0) return 0;
    final elapsed = now.difference(start).inSeconds;
    return (elapsed / total).clamp(0.0, 1.0);
  }

  String _liveStatusText(LessonModel lesson) {
    final now = DateTime.now();
    final start = _parseLessonDateTime(lesson.date, lesson.timeStart);
    final end = _parseLessonDateTime(lesson.date, lesson.timeEnd);
    if (start == null || end == null) {
      return '${lesson.timeStart}\u2013${lesson.timeEnd}';
    }

    if (now.isAfter(start) && now.isBefore(end)) {
      final minsLeft = end.difference(now).inMinutes + 1;
      return 'Йде зараз \u2022 до кінця $minsLeft хв';
    }
    if (now.isBefore(start)) {
      final diff = start.difference(now);
      if (diff.inHours >= 24) {
        final days = diff.inDays;
        return 'Заплановано (${lesson.date}, через $days дн.)';
      }
      final minsToStart = diff.inMinutes + 1;
      return 'Початок через $minsToStart хв';
    }
    return 'Завершено';
  }

  LessonModel? _findActiveOrNextLesson(List<LessonModel> allLessons) {
    final todayStr = _formatDateDdMmYyyy(DateTime.now());
    final todayItems = allLessons.where((l) => l.date.trim() == todayStr).toList()
      ..sort((a, b) => a.lessonNumber.compareTo(b.lessonNumber));
    if (todayItems.isEmpty) return null;

    final now = DateTime.now();
    for (final l in todayItems) {
      final start = _parseLessonDateTime(l.date, l.timeStart);
      final end = _parseLessonDateTime(l.date, l.timeEnd);
      if (start != null && end != null && now.isAfter(start) && now.isBefore(end)) {
        return l;
      }
    }
    for (final l in todayItems) {
      final start = _parseLessonDateTime(l.date, l.timeStart);
      if (start != null && now.isBefore(start)) return l;
    }
    return null;
  }

  List<LessonModel> _buildVisibleLessons(String activeDate) {
    if (_showAllDates) {
      final copy = [..._lessons];
      copy.sort((a, b) {
        final dtA = _tryParseDdMmYyyy(a.date) ?? DateTime(1970);
        final dtB = _tryParseDdMmYyyy(b.date) ?? DateTime(1970);
        final cmp = dtA.compareTo(dtB);
        if (cmp != 0) return cmp;
        return a.lessonNumber.compareTo(b.lessonNumber);
      });
      return copy;
    }

    final dayItems = _lessons.where((l) => l.date.trim() == activeDate.trim()).toList()
      ..sort((a, b) => a.lessonNumber.compareTo(b.lessonNumber));
    return dayItems;
  }

  @override
  Widget build(BuildContext context) {
    final dates = _sortedDates(_lessons, fromTodayOnly: true);
    final todayStr = _formatDateDdMmYyyy(DateTime.now());
    final activeDate = _selectedDate ?? todayStr;
    final visibleLessons = _buildVisibleLessons(activeDate);
    final featuredLesson = _findActiveOrNextLesson(_lessons);

    return Scaffold(
      appBar: AppBar(
        title: DropdownButtonHideUnderline(
          child: DropdownButton<String>(
            value: _groups.contains(_selectedGroup) ? _selectedGroup : null,
            hint: Text(_selectedGroup),
            items: _groups
                .map((g) => DropdownMenuItem<String>(value: g, child: Text(g)))
                .toList(),
            onChanged: _onGroupChanged,
          ),
        ),
        actions: [
          IconButton(
            tooltip: 'Відкрити Moodle',
            onPressed: () => _openExternalUrl(kMoodleUrl),
            icon: const Icon(Icons.school_outlined),
          ),
          IconButton(
            tooltip: 'Оновити розклад',
            onPressed: () => _fetchSchedule(null),
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: Column(
        children: [
          if (_offlineBanner)
            Container(
              width: double.infinity,
              color: Theme.of(context).colorScheme.secondaryContainer,
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
              child: const Text(
                'Офлайн-режим: показано збережений розклад',
                style: TextStyle(fontSize: 12),
              ),
            ),
          if (featuredLesson != null)
            Card(
              margin: const EdgeInsets.fromLTRB(12, 10, 12, 4),
              color: Theme.of(context).colorScheme.primaryContainer,
              child: Padding(
                padding: const EdgeInsets.all(12),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      _liveStatusText(featuredLesson).toUpperCase(),
                      style: const TextStyle(
                        fontSize: 11,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      '${featuredLesson.lessonNumber} пара (${featuredLesson.timeStart}\u2013${featuredLesson.timeEnd}) \u2022 ${featuredLesson.subject}',
                      style: const TextStyle(fontWeight: FontWeight.w600),
                    ),
                    const SizedBox(height: 8),
                    LinearProgressIndicator(value: _lessonProgress(featuredLesson)),
                    if (_getZoomConfig(featuredLesson) != null) ...[
                      const SizedBox(height: 8),
                      FilledButton.icon(
                        onPressed: () => _launchZoom(_getZoomConfig(featuredLesson)!),
                        icon: const Icon(Icons.videocam, size: 18),
                        label: const Text('Увійти в Zoom'),
                      ),
                    ],
                  ],
                ),
              ),
            ),
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 8, 12, 0),
            child: Row(
              children: [
                ActionChip(
                  label: const Text('Сьогодні'),
                  onPressed: () => setState(() {
                    _showAllDates = false;
                    _selectedDate = todayStr;
                  }),
                ),
                const SizedBox(width: 8),
                FilterChip(
                  label: const Text('Усі дні'),
                  selected: _showAllDates,
                  onSelected: (val) => setState(() => _showAllDates = val),
                ),
              ],
            ),
          ),
          if (!_showAllDates && dates.isNotEmpty)
            SizedBox(
              height: 48,
              child: ListView.separated(
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                scrollDirection: Axis.horizontal,
                itemCount: dates.length,
                separatorBuilder: (_, __) => const SizedBox(width: 8),
                itemBuilder: (context, idx) {
                  final d = dates[idx];
                  final selected = d == activeDate;
                  return ChoiceChip(
                    label: Text(d == todayStr ? '$d (сьогодні)' : d),
                    selected: selected,
                    onSelected: (_) => setState(() {
                      _showAllDates = false;
                      _selectedDate = d;
                    }),
                  );
                },
              ),
            ),
          Expanded(
            child: _loading
                ? const Center(child: CircularProgressIndicator())
                : _error != null
                    ? Center(
                        child: Padding(
                          padding: const EdgeInsets.all(16),
                          child: Text(_error!, textAlign: TextAlign.center),
                        ),
                      )
                    : visibleLessons.isEmpty
                        ? Center(
                            child: Padding(
                              padding: const EdgeInsets.all(24),
                              child: Column(
                                mainAxisSize: MainAxisSize.min,
                                children: [
                                  const Icon(Icons.event_available, size: 48),
                                  const SizedBox(height: 12),
                                  Text(
                                    'Цього дня ($activeDate) ви вільні',
                                    style: const TextStyle(
                                      fontSize: 16,
                                      fontWeight: FontWeight.w600,
                                    ),
                                    textAlign: TextAlign.center,
                                  ),
                                  const SizedBox(height: 12),
                                  OutlinedButton(
                                    onPressed: () =>
                                        setState(() => _showAllDates = true),
                                    child: const Text('Показати всі дні'),
                                  ),
                                ],
                              ),
                            ),
                          )
                        : ListView.builder(
                            padding: const EdgeInsets.all(12),
                            itemCount: visibleLessons.length,
                            itemBuilder: (context, index) {
                              final item = visibleLessons[index];
                              final zoomCfg = _getZoomConfig(item);
                              final note = _lessonNotes[buildNoteKey(item)];
                              final metaParts = <String>[
                                '${item.lessonNumber} пара (${item.timeStart}–${item.timeEnd})',
                                if (item.lessonType != null && item.lessonType!.trim().isNotEmpty)
                                  item.lessonType!.trim(),
                                if (item.room != null && item.room!.trim().isNotEmpty)
                                  item.room!.trim(),
                                if (displayTeacher(item.teacher) != null)
                                  displayTeacher(item.teacher)!,
                              ];

                              return Card(
                                margin: const EdgeInsets.only(bottom: 10),
                                child: Padding(
                                  padding: const EdgeInsets.all(12),
                                  child: Column(
                                    crossAxisAlignment: CrossAxisAlignment.start,
                                    children: [
                                      Text(
                                        item.subject,
                                        style: const TextStyle(
                                          fontSize: 16,
                                          fontWeight: FontWeight.w600,
                                        ),
                                      ),
                                      const SizedBox(height: 4),
                                      Text('${item.date} \u2022 ${metaParts.join(' \u2022 ')}'),
                                      const SizedBox(height: 4),
                                      Text(
                                        _liveStatusText(item),
                                        style: Theme.of(context).textTheme.bodySmall,
                                      ),
                                      if (zoomCfg != null &&
                                          zoomCfg.mode == 'credentials') ...[
                                        const SizedBox(height: 8),
                                        Wrap(
                                          spacing: 8,
                                          runSpacing: 6,
                                          children: [
                                            if ((zoomCfg.meetingCode ?? '').isNotEmpty)
                                              ActionChip(
                                                avatar: const Icon(Icons.copy, size: 16),
                                                label: Text('Код: ${zoomCfg.meetingCode}'),
                                                onPressed: () => _copyText(
                                                  'Код конференції',
                                                  zoomCfg.meetingCode!,
                                                ),
                                              ),
                                            if ((zoomCfg.password ?? '').isNotEmpty)
                                              ActionChip(
                                                avatar: const Icon(Icons.copy, size: 16),
                                                label: Text('Пароль: ${zoomCfg.password}'),
                                                onPressed: () => _copyText(
                                                  'Пароль конференції',
                                                  zoomCfg.password!,
                                                ),
                                              ),
                                          ],
                                        ),
                                      ],
                                      if (note != null && note.isNotEmpty) ...[
                                        const SizedBox(height: 6),
                                        Container(
                                          width: double.infinity,
                                          padding: const EdgeInsets.all(8),
                                          decoration: BoxDecoration(
                                            color: Theme.of(context)
                                                .colorScheme
                                                .surfaceContainerHighest,
                                            borderRadius: BorderRadius.circular(8),
                                          ),
                                          child: Text('Нотатка: $note'),
                                        ),
                                      ],
                                      const SizedBox(height: 10),
                                      Wrap(
                                        spacing: 8,
                                        runSpacing: 8,
                                        children: [
                                          if (zoomCfg != null)
                                            FilledButton.icon(
                                              onPressed: () => _launchZoom(zoomCfg),
                                              icon: const Icon(Icons.videocam, size: 18),
                                              label: const Text('Увійти в Zoom'),
                                            ),
                                          OutlinedButton.icon(
                                            onPressed: () => _showZoomDialog(item),
                                            icon: Icon(
                                              zoomCfg == null
                                                  ? Icons.add_link
                                                  : Icons.edit,
                                              size: 18,
                                            ),
                                            label: Text(
                                              zoomCfg == null
                                                  ? 'Додати Zoom з Moodle'
                                                  : 'Змінити Zoom',
                                            ),
                                          ),
                                          OutlinedButton.icon(
                                            onPressed: () => _showNoteDialog(item),
                                            icon: const Icon(
                                              Icons.note_add_outlined,
                                              size: 18,
                                            ),
                                            label: Text(
                                              note == null || note.isEmpty
                                                  ? 'Нотатка'
                                                  : 'Змінити нотатку',
                                            ),
                                          ),
                                          if (zoomCfg == null)
                                            TextButton.icon(
                                              onPressed: () => _openExternalUrl(kMoodleUrl),
                                              icon: const Icon(Icons.open_in_new, size: 18),
                                              label: const Text('Moodle'),
                                            ),
                                        ],
                                      ),
                                    ],
                                  ),
                                ),
                              );
                            },
                          ),
          ),
        ],
      ),
    );
  }
}

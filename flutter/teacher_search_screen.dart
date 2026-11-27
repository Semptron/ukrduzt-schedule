import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import 'package:url_launcher/url_launcher.dart';
import 'api_config.dart' show kApiBaseUrl, kHttpHeaders, kMetadataTimeout, kScheduleTimeout;
import 'schedule_screen.dart';


bool _looksLikeTeacher(String name) {
  final t = name.trim();
  if (t.length < 4 || t.length > 60) return false;
  // room codes like 1.125б / 2.220б
  if (RegExp(r'^\d+\.[\wА-Яа-яІіЇїЄєҐґ]+$').hasMatch(t)) return false;
  if (t.toLowerCase().contains('http') || t.toLowerCase().contains('zoom')) {
    return false;
  }
  if (RegExp(r'^[A-Za-z0-9+/=_-]{16,}$').hasMatch(t)) return false;
  // must contain a Cyrillic letter (Ukrainian names)
  if (!RegExp(r'[А-Яа-яІіЇїЄєҐґ]').hasMatch(t)) return false;
  // prefer "Surname X." pattern
  return RegExp(r"^[А-ЯІЇЄҐ][а-яіїєґ'’`-]+\s+[А-ЯІЇЄҐ]").hasMatch(t);
}

class TeacherSearchScreen extends StatefulWidget {
  final String apiBaseUrl;
  final bool isActive;

  const TeacherSearchScreen({
    super.key,
    this.apiBaseUrl = kApiBaseUrl,
    this.isActive = true,
  });

  @override
  State<TeacherSearchScreen> createState() => _TeacherSearchScreenState();
}

class _TeacherSearchScreenState extends State<TeacherSearchScreen>
    with WidgetsBindingObserver {
  final TextEditingController _searchController = TextEditingController();
  List<String> _allTeachers = [];
  List<LessonModel> _results = [];
  Map<String, ZoomAccessConfig> _zoomConfigs = {};
  bool _loadingTeachers = true;
  bool _searching = false;
  String? _error;
  int _searchSeq = 0;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _loadLocalZoomAndTeachers();
  }

  @override
  void didUpdateWidget(covariant TeacherSearchScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    // When the user switches back to this tab, re-read Zoom overrides that may
    // have been added on the schedule tab meanwhile.
    if (!oldWidget.isActive && widget.isActive) {
      _refreshZoomConfigs();
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _searchController.dispose();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(_) {
    // App resumed from background — re-read Zoom overrides that the user may
    // have added on the schedule tab while the app was backgrounded.
    _refreshZoomConfigs();
  }

  Future<void> _refreshZoomConfigs() async {
    final prefs = await SharedPreferences.getInstance();
    final rawMeetings = prefs.getString(kMeetingsPrefsKey);
    Map<String, ZoomAccessConfig> next = {};
    if (rawMeetings != null && rawMeetings.isNotEmpty) {
      try {
        final decoded = jsonDecode(rawMeetings) as Map<String, dynamic>;
        next = decoded.map(
          (k, v) => MapEntry(
            k,
            ZoomAccessConfig.fromJson(v as Map<String, dynamic>),
          ),
        );
      } catch (_) {}
    }
    if (!mounted) return;
    setState(() => _zoomConfigs = next);
  }

  List<LessonModel> _collectCachedLessons(SharedPreferences prefs) {
    final lessons = <LessonModel>[];
    for (final key in prefs.getKeys()) {
      if (!key.startsWith('ukrduzt_schedule_cache_')) continue;
      final raw = prefs.getString(key);
      if (raw == null || raw.isEmpty) continue;
      try {
        final decoded = jsonDecode(raw) as List<dynamic>;
        for (final item in decoded) {
          lessons.add(LessonModel.fromJson(item as Map<String, dynamic>));
        }
      } catch (_) {}
    }
    return lessons;
  }

  Future<void> _loadLocalZoomAndTeachers() async {
    final prefs = await SharedPreferences.getInstance();

    final rawMeetings = prefs.getString(kMeetingsPrefsKey);
    if (rawMeetings != null && rawMeetings.isNotEmpty) {
      try {
        final decoded = jsonDecode(rawMeetings) as Map<String, dynamic>;
        _zoomConfigs = decoded.map(
          (k, v) => MapEntry(
            k,
            ZoomAccessConfig.fromJson(v as Map<String, dynamic>),
          ),
        );
      } catch (_) {}
    }

    final cachedLessons = _collectCachedLessons(prefs);
    final localTeachers = <String>{};
    for (final lesson in cachedLessons) {
      final t = (lesson.teacher ?? '').trim();
      if (t.isNotEmpty) localTeachers.add(t);
    }

    if (mounted) {
      setState(() {
        _allTeachers = localTeachers.where(_looksLikeTeacher).toList()..sort();
        _loadingTeachers = false;
      });
    }

    try {
      final uri = Uri.parse('${widget.apiBaseUrl}/api/v1/teachers');
      final resp = await http.get(uri, headers: kHttpHeaders).timeout(kMetadataTimeout);
      if (resp.statusCode == 200 && mounted) {
        final data = jsonDecode(resp.body) as Map<String, dynamic>;
        final apiTeachers = (data['teachers'] as List<dynamic>? ?? [])
            .map((e) => e.toString().trim())
            .where((e) => e.isNotEmpty)
            .toSet();
        apiTeachers.addAll(localTeachers);
        final filtered = apiTeachers.where(_looksLikeTeacher).toList()..sort();
        setState(() => _allTeachers = filtered);
      }
    } catch (_) {}
  }

  Future<void> _searchByTeacher(String query) async {
    final seq = ++_searchSeq;
    final cleaned = query.trim();
    if (cleaned.isEmpty) {
      if (!mounted) return;
      setState(() {
        _results = [];
        _error = null;
      });
      return;
    }

    setState(() {
      _searching = true;
      _error = null;
    });

    try {
      final uri = Uri.parse(
        '${widget.apiBaseUrl}/api/v1/schedule?teacher=${Uri.encodeComponent(cleaned)}',
      );
      final resp = await http.get(uri, headers: kHttpHeaders).timeout(kScheduleTimeout);
      if (!mounted || seq != _searchSeq) return;

      if (resp.statusCode == 404) {
        setState(() {
          _results = [];
          _searching = false;
          _error = 'Занять для викладача "$cleaned" не знайдено';
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

      setState(() {
        _results = items;
        _searching = false;
        _error = null;
      });
    } catch (_) {
      final prefs = await SharedPreferences.getInstance();
      if (!mounted || seq != _searchSeq) return;
      final qLower = cleaned.toLowerCase();
      final fallback = _collectCachedLessons(prefs)
          .where((lesson) => (lesson.teacher ?? '').toLowerCase().contains(qLower))
          .toList();

      setState(() {
        _results = fallback;
        _searching = false;
        _error = fallback.isEmpty
            ? 'Немає зв’язку і локальних даних для "$cleaned"'
            : null;
      });
    }
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

  Future<void> _openUrl(String url) async {
    final normalized =
        url.startsWith('http://') || url.startsWith('https://') ? url : 'https://$url';
    final uri = Uri.parse(normalized);
    if (!await launchUrl(uri, mode: LaunchMode.externalApplication) && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Не вдалося відкрити: $normalized')),
      );
    }
  }

  Future<void> _launchZoom(ZoomAccessConfig cfg) async {
    if (cfg.mode == 'link' && (cfg.url ?? '').trim().isNotEmpty) {
      await _openUrl(cfg.url!.trim());
      return;
    }
    final code = (cfg.meetingCode ?? '').replaceAll(RegExp(r'\s+'), '');
    if (code.isNotEmpty) {
      final pwd = (cfg.password ?? '').trim();
      final suffix = pwd.isNotEmpty ? '?pwd=${Uri.encodeComponent(pwd)}' : '';
      await _openUrl('https://zoom.us/j/$code$suffix');
      return;
    }
    await _openUrl(kZoomAppUrl);
  }

  Future<void> _copy(String label, String val) async {
    await Clipboard.setData(ClipboardData(text: val));
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text('$label скопійовано: $val')),
    );
  }

  @override
  Widget build(BuildContext context) {
    final filterText = _searchController.text.trim().toLowerCase();
    final filteredTeachers = filterText.isEmpty
        ? _allTeachers.take(12).toList()
        : _allTeachers.where((t) => t.toLowerCase().contains(filterText)).take(12).toList();

    return Scaffold(
      appBar: AppBar(
        title: const Text('Пошук за викладачем'),
        actions: [
          IconButton(
            tooltip: 'Відкрити Moodle',
            onPressed: () => _openUrl(kMoodleUrl),
            icon: const Icon(Icons.school_outlined),
          ),
        ],
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 12, 12, 6),
            child: TextField(
              controller: _searchController,
              onChanged: (_) => setState(() {}),
              onSubmitted: _searchByTeacher,
              decoration: InputDecoration(
                labelText: 'ПІБ викладача',
                hintText: 'Наприклад: Близнюк або Бабенко',
                prefixIcon: const Icon(Icons.search),
                suffixIcon: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    if (_searchController.text.isNotEmpty)
                      IconButton(
                        icon: const Icon(Icons.clear),
                        onPressed: () {
                          _searchSeq++;
                          _searchController.clear();
                          setState(() {
                            _results = [];
                            _error = null;
                          });
                        },
                      ),
                    IconButton(
                      icon: const Icon(Icons.send),
                      onPressed: () => _searchByTeacher(_searchController.text),
                    ),
                  ],
                ),
                border: const OutlineInputBorder(),
              ),
            ),
          ),
          if (_loadingTeachers)
            const LinearProgressIndicator()
          else if (filteredTeachers.isNotEmpty)
            SizedBox(
              height: 44,
              child: ListView.separated(
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
                scrollDirection: Axis.horizontal,
                itemCount: filteredTeachers.length,
                separatorBuilder: (_, __) => const SizedBox(width: 8),
                itemBuilder: (context, index) {
                  final teacher = filteredTeachers[index];
                  return ActionChip(
                    label: Text(teacher),
                    onPressed: () {
                      _searchController.text = teacher;
                      setState(() {});
                      _searchByTeacher(teacher);
                    },
                  );
                },
              ),
            ),
          Expanded(
            child: _searching
                ? const Center(child: CircularProgressIndicator())
                : _error != null
                    ? Center(
                        child: Padding(
                          padding: const EdgeInsets.all(16),
                          child: Text(_error!, textAlign: TextAlign.center),
                        ),
                      )
                    : _results.isEmpty
                        ? const Center(
                            child: Text('Введіть ПІБ викладача або оберіть зі списку'),
                          )
                        : ListView.builder(
                            padding: const EdgeInsets.all(12),
                            itemCount: _results.length,
                            itemBuilder: (context, index) {
                              final item = _results[index];
                              final zoomCfg = _getZoomConfig(item);
                              final meta = <String>[
                                  if (item.group.trim().isNotEmpty) 'Група ${item.group}',
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
                                      Text('${item.date} \u2022 ${meta.join(' \u2022 ')}'),
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
                                                onPressed: () => _copy(
                                                  'Код конференції',
                                                  zoomCfg.meetingCode!,
                                                ),
                                              ),
                                            if ((zoomCfg.password ?? '').isNotEmpty)
                                              ActionChip(
                                                avatar: const Icon(Icons.copy, size: 16),
                                                label: Text('Пароль: ${zoomCfg.password}'),
                                                onPressed: () => _copy(
                                                  'Пароль конференції',
                                                  zoomCfg.password!,
                                                ),
                                              ),
                                          ],
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
                                            )
                                          else
                                            OutlinedButton.icon(
                                              onPressed: () => _openUrl(kMoodleUrl),
                                              icon: const Icon(Icons.open_in_new, size: 18),
                                              label: const Text('Знайти в Moodle'),
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

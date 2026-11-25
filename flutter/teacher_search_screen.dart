import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import 'package:url_launcher/url_launcher.dart';
import 'schedule_screen.dart';

class TeacherSearchScreen extends StatefulWidget {
  final String apiBaseUrl;

  const TeacherSearchScreen({
    super.key,
    this.apiBaseUrl = 'http://10.0.2.2:8000',
  });

  @override
  State<TeacherSearchScreen> createState() => _TeacherSearchScreenState();
}

class _TeacherSearchScreenState extends State<TeacherSearchScreen> {
  final TextEditingController _searchController = TextEditingController();
  List<String> _allTeachers = [];
  List<LessonModel> _results = [];
  Map<String, ZoomAccessConfig> _zoomConfigs = {};
  bool _loadingTeachers = true;
  bool _searching = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadLocalZoomAndTeachers();
  }

  @override
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }

  Future<void> _loadLocalZoomAndTeachers() async {
    final prefs = await SharedPreferences.getInstance();

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

    final localTeachers = <String>{};
    for (final key in prefs.getKeys()) {
      if (key.startsWith('ukrduzt_schedule_cache_')) {
        final raw = prefs.getString(key);
        if (raw != null && raw.isNotEmpty) {
          for (final item in (jsonDecode(raw) as List<dynamic>)) {
            final t = (item as Map<String, dynamic>)['teacher']?.toString().trim();
            if (t != null && t.isNotEmpty) localTeachers.add(t);
          }
        }
      }
    }

    setState(() {
      _allTeachers = localTeachers.toList()..sort();
      _loadingTeachers = false;
    });

    try {
      final uri = Uri.parse('${widget.apiBaseUrl}/api/v1/teachers');
      final resp = await http.get(uri).timeout(const Duration(seconds: 8));
      if (resp.statusCode == 200) {
        final data = jsonDecode(resp.body) as Map<String, dynamic>;
        final apiTeachers = (data['teachers'] as List<dynamic>)
            .map((e) => e.toString().trim())
            .where((e) => e.isNotEmpty)
            .toSet();
        apiTeachers.addAll(localTeachers);
        if (mounted) {
          setState(() => _allTeachers = apiTeachers.toList()..sort());
        }
      }
    } catch (_) {}
  }

  Future<void> _searchByTeacher(String query) async {
    final cleaned = query.trim();
    if (cleaned.isEmpty) {
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
      final resp = await http.get(uri).timeout(const Duration(seconds: 10));
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
      final items = (data['items'] as List<dynamic>)
          .map((e) => LessonModel.fromJson(e as Map<String, dynamic>))
          .toList();

      if (!mounted) return;
      setState(() {
        _results = items;
        _searching = false;
        _error = null;
      });
    } catch (_) {
      final prefs = await SharedPreferences.getInstance();
      final qLower = cleaned.toLowerCase();
      final fallback = <LessonModel>[];

      for (final key in prefs.getKeys()) {
        if (key.startsWith('ukrduzt_schedule_cache_')) {
          final raw = prefs.getString(key);
          if (raw == null || raw.isEmpty) continue;
          for (final e in (jsonDecode(raw) as List<dynamic>)) {
            final lesson = LessonModel.fromJson(e as Map<String, dynamic>);
            if ((lesson.teacher ?? '').toLowerCase().contains(qLower)) {
              fallback.add(lesson);
            }
          }
        }
      }

      if (!mounted) return;
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
      

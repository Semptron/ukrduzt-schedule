import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:file_picker/file_picker.dart';
import 'package:http/http.dart' as http;
import 'package:http_parser/http_parser.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'api_config.dart' show kApiBaseUrl, kHttpHeaders, kMetadataTimeout, kScheduleTimeout;

/// SharedPreferences key for the optional X-Sync-Token the user enters to
/// authenticate manual uploads against a production backend that has
/// SYNC_TOKEN set. Stored locally so the user doesn't retype it each time.
const String kSyncTokenPrefsKey = 'ukrduzt_sync_token_v1';

/// One entry in the list of manually-uploaded schedule files.
class ManualUploadEntry {
  final int id;
  final String originalName;
  final String sha256;
  final int lessonCount;
  final int warningsCount;
  final String uploadedAt;
  final String? note;

  ManualUploadEntry({
    required this.id,
    required this.originalName,
    required this.sha256,
    required this.lessonCount,
    required this.warningsCount,
    required this.uploadedAt,
    this.note,
  });

  factory ManualUploadEntry.fromJson(Map<String, dynamic> json) {
    return ManualUploadEntry(
      id: json['id'] as int,
      originalName: (json['original_name'] as String?) ?? '',
      sha256: (json['sha256'] as String?) ?? '',
      lessonCount: (json['lesson_count'] as int?) ?? 0,
      warningsCount: (json['warnings_count'] as int?) ?? 0,
      uploadedAt: (json['uploaded_at'] as String?) ?? '',
      note: json['note'] as String?,
    );
  }
}

/// Result of an upload attempt — either the server's success payload or a
/// human-readable error message. Kept as a plain class (not Either) so the
/// widget tree can switch on `.ok` without a dependency.
class UploadResult {
  final bool ok;
  final String? message;
  final int? lessonsAdded;
  final int? warningsCount;
  final int? manualId;

  UploadResult.ok({
    required this.lessonsAdded,
    required this.warningsCount,
    required this.manualId,
  })  : ok = true,
        message = null;

  UploadResult.error(this.message)
      : ok = false,
        lessonsAdded = null,
        warningsCount = null,
        manualId = null;
}

/// Stateful screen that lets a trusted user (curator) push a PDF schedule
/// onto the backend so its rows override the portal's for the same
/// (date, group, lesson_number). The backend gates this behind X-Sync-Token
/// when SYNC_TOKEN is configured; we store that token locally.
class ManualUploadScreen extends StatefulWidget {
  final String apiBaseUrl;

  const ManualUploadScreen({super.key, this.apiBaseUrl = kApiBaseUrl});

  @override
  State<ManualUploadScreen> createState() => _ManualUploadScreenState();
}

class _ManualUploadScreenState extends State<ManualUploadScreen> {
  final TextEditingController _tokenController = TextEditingController();
  final TextEditingController _noteController = TextEditingController();
  String? _selectedFileName;
  int? _selectedFileSize;
  String? _selectedFilePath;
  bool _uploading = false;
  bool _loadingList = true;
  String? _statusMessage;
  bool _statusOk = false;
  List<ManualUploadEntry> _uploads = [];

  @override
  void initState() {
    super.initState();
    _loadTokenAndRefresh();
  }

  @override
  void dispose() {
    _tokenController.dispose();
    _noteController.dispose();
    super.dispose();
  }

  Future<void> _loadTokenAndRefresh() async {
    final prefs = await SharedPreferences.getInstance();
    final saved = prefs.getString(kSyncTokenPrefsKey);
    if (saved != null && saved.isNotEmpty && mounted) {
      _tokenController.text = saved;
    }
    await _refreshList();
  }

  Map<String, String> get _authHeaders {
    final token = _tokenController.text.trim();
    final h = Map<String, String>.from(kHttpHeaders);
    if (token.isNotEmpty) h['X-Sync-Token'] = token;
    return h;
  }

  Future<void> _pickFile() async {
    setState(() => _statusMessage = null);
    try {
      final result = await FilePicker.platform.pickFiles(
        type: FileType.custom,
        allowedExtensions: ['pdf'],
        withData: false,
      );
      if (result == null || result.files.isEmpty) return;
      final f = result.files.single;
      if (!mounted) return;
      setState(() {
        _selectedFileName = f.name;
        _selectedFileSize = f.size;
        _selectedFilePath = f.path;
        _statusMessage = null;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _statusMessage = 'Не вдалося вибрати файл: $e';
        _statusOk = false;
      });
    }
  }

  Future<void> _upload() async {
    if (_selectedFilePath == null || _selectedFileName == null) {
      setState(() {
        _statusMessage = 'Спочатку виберіть PDF-файл';
        _statusOk = false;
      });
      return;
    }
    setState(() {
      _uploading = true;
      _statusMessage = null;
    });

    final prefs = await SharedPreferences.getInstance();
    final token = _tokenController.text.trim();
    if (token.isNotEmpty) {
      await prefs.setString(kSyncTokenPrefsKey, token);
    }

    try {
      final uri = Uri.parse('${widget.apiBaseUrl}/api/v1/admin/upload');
      final request = http.MultipartRequest('POST', uri)
        ..headers.addAll(_authHeaders)
        ..fields['note'] = _noteController.text.trim()
        ..files.add(
          await http.MultipartFile.fromPath(
            'file',
            _selectedFilePath!,
            filename: _selectedFileName,
            contentType: MediaType('application', 'pdf'),
          ),
        );

      final streamed = await request.send().timeout(kScheduleTimeout);
      final resp = await http.Response.fromStream(streamed);
      final body = jsonDecode(resp.body) as Map<String, dynamic>;

      if (resp.statusCode == 201) {
        if (!mounted) return;
        setState(() {
          _statusOk = true;
          _statusMessage =
              'Завантажено: ${body['lessons_added']} пар${body['warnings_count'] != null && (body['warnings_count'] as int) > 0 ? ", попереджень: ${body['warnings_count']}" : ""}.';
          _selectedFileName = null;
          _selectedFileSize = null;
          _selectedFilePath = null;
          _noteController.clear();
          _uploading = false;
        });
        await _refreshList();
      } else {
        if (!mounted) return;
        final detail = body['detail'] ?? 'Помилка ${resp.statusCode}';
        setState(() {
          _statusOk = false;
          _statusMessage = detail.toString();
          _uploading = false;
        });
      }
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _statusOk = false;
        _statusMessage = 'Помилка завантаження: $e';
        _uploading = false;
      });
    }
  }

  Future<void> _refreshList() async {
    setState(() => _loadingList = true);
    try {
      final uri = Uri.parse('${widget.apiBaseUrl}/api/v1/admin/uploads');
      final resp = await http.get(uri, headers: _authHeaders)
          .timeout(kMetadataTimeout);
      if (!mounted) return;
      if (resp.statusCode == 200) {
        final data = jsonDecode(resp.body) as Map<String, dynamic>;
        final list = (data['uploads'] as List<dynamic>? ?? [])
            .map((e) => ManualUploadEntry.fromJson(e as Map<String, dynamic>))
            .toList();
        setState(() {
          _uploads = list;
          _loadingList = false;
        });
      } else if (resp.statusCode == 403) {
        setState(() {
          _uploads = [];
          _loadingList = false;
          if (_statusMessage == null) {
            _statusMessage = 'Потрібен дійсний X-Sync-Token для перегляду списку';
            _statusOk = false;
          }
        });
      } else {
        setState(() {
          _loadingList = false;
        });
      }
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loadingList = false;
        _uploads = [];
      });
    }
  }

  Future<void> _deleteEntry(int id) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Видалити файл?'),
        content: const Text(
          'Це видалить усі пари з цього файлу. Для тих самих дат і груп знову '
          'з\'явиться розклад з порталу.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Скасувати'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Видалити'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;

    try {
      final uri = Uri.parse('${widget.apiBaseUrl}/api/v1/admin/uploads/$id');
      final resp = await http.delete(uri, headers: _authHeaders)
          .timeout(kMetadataTimeout);
      if (!mounted) return;
      if (resp.statusCode == 200) {
        setState(() {
          _statusOk = true;
          _statusMessage = 'Файл видалено';
        });
        await _refreshList();
      } else {
        final body = jsonDecode(resp.body) as Map<String, dynamic>;
        setState(() {
          _statusOk = false;
          _statusMessage = (body['detail'] ?? 'Помилка ${resp.statusCode}').toString();
        });
      }
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _statusOk = false;
        _statusMessage = 'Помилка видалення: $e';
      });
    }
  }

  String _formatSize(int bytes) {
    if (bytes < 1024) return '$bytes Б';
    if (bytes < 1024 * 1024) return '${(bytes / 1024).toStringAsFixed(1)} КБ';
    return '${(bytes / (1024 * 1024)).toStringAsFixed(1)} МБ';
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Додати розклад з файлу'),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            onPressed: _refreshList,
            tooltip: 'Оновити список',
          ),
        ],
      ),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            _introCard(),
            const SizedBox(height: 16),
            _tokenField(),
            const SizedBox(height: 12),
            _filePickerRow(),
            const SizedBox(height: 12),
            _noteField(),
            const SizedBox(height: 16),
            _uploadButton(),
            if (_statusMessage != null) ...[
              const SizedBox(height: 12),
              _statusBanner(),
            ],
            const SizedBox(height: 24),
            Text(
              'Завантажені файли',
              style: Theme.of(context).textTheme.titleMedium,
            ),
            const SizedBox(height: 8),
            _uploadsList(),
          ],
        ),
      ),
    );
  }

  Widget _introCard() {
    return Card(
      color: Theme.of(context).colorScheme.primaryContainer,
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(Icons.info_outline,
                color: Theme.of(context).colorScheme.onPrimaryContainer),
            const SizedBox(width: 8),
            Expanded(
              child: Text(
                'Якщо розклад на сайті не збігається з тим, що скинув куратор — '
                'завантажте PDF сюди. Пари з цього файлу замінять сайтні для тих '
                'самих дат і груп. Якщо видалите файл — знову з\'явиться розклад з '
                'порталу.',
                style: TextStyle(
                  color: Theme.of(context).colorScheme.onPrimaryContainer,
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _tokenField() {
    return TextField(
      controller: _tokenController,
      decoration: InputDecoration(
        labelText: 'X-Sync-Token (необов\'язково)',
        hintText: 'лише якщо на сервері встановлено SYNC_TOKEN',
        border: const OutlineInputBorder(),
        prefixIcon: const Icon(Icons.vpn_key_outlined),
      ),
      onChanged: (_) => setState(() => _statusMessage = null),
    );
  }

  Widget _noteField() {
    return TextField(
      controller: _noteController,
      decoration: InputDecoration(
        labelText: 'Помітка (необов\'язково)',
        hintText: 'напр. «розклад від куратора 24.11»',
        border: const OutlineInputBorder(),
        prefixIcon: const Icon(Icons.note_outlined),
      ),
    );
  }

  Widget _filePickerRow() {
    return Row(
      children: [
        Expanded(
          child: OutlinedButton.icon(
            onPressed: _uploading ? null : _pickFile,
            icon: const Icon(Icons.attach_file),
            label: const Text('Вибрати PDF'),
          ),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: Text(
            _selectedFileName == null
                ? 'Файл не вибрано'
                : '$_selectedFileName'
                    '${_selectedFileSize != null ? '  ·  ${_formatSize(_selectedFileSize!)}' : ''}',
            overflow: TextOverflow.ellipsis,
            style: Theme.of(context).textTheme.bodyMedium,
          ),
        ),
      ],
    );
  }

  Widget _uploadButton() {
    return FilledButton.icon(
      onPressed: _uploading || _selectedFilePath == null ? null : _upload,
      icon: _uploading
          ? const SizedBox(
              width: 18,
              height: 18,
              child: CircularProgressIndicator(strokeWidth: 2),
            )
          : const Icon(Icons.cloud_upload),
      label: Text(_uploading ? 'Завантаження…' : 'Завантажити розклад'),
    );
  }

  Widget _statusBanner() {
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: _statusOk
            ? Theme.of(context).colorScheme.tertiaryContainer
            : Theme.of(context).colorScheme.errorContainer,
        borderRadius: BorderRadius.circular(8),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(
            _statusOk ? Icons.check_circle : Icons.error_outline,
            color: _statusOk
                ? Theme.of(context).colorScheme.onTertiaryContainer
                : Theme.of(context).colorScheme.onErrorContainer,
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              _statusMessage ?? '',
              style: TextStyle(
                color: _statusOk
                    ? Theme.of(context).colorScheme.onTertiaryContainer
                    : Theme.of(context).colorScheme.onErrorContainer,
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _uploadsList() {
    if (_loadingList) {
      return const Padding(
        padding: EdgeInsets.symmetric(vertical: 24),
        child: Center(child: CircularProgressIndicator()),
      );
    }
    if (_uploads.isEmpty) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 24),
        child: Center(
          child: Text(
            'Поки що жодного файлу не завантажено',
            style: Theme.of(context).textTheme.bodyMedium,
          ),
        ),
      );
    }
    return Column(
      children: _uploads.map((e) => _uploadTile(e)).toList(),
    );
  }

  Widget _uploadTile(ManualUploadEntry e) {
    return Card(
      child: ListTile(
        leading: const Icon(Icons.picture_as_pdf),
        title: Text(e.originalName),
        subtitle: Text(
          [
            if (e.note != null && e.note!.isNotEmpty) '“${e.note}”',
            '${e.lessonCount} пар',
            if (e.warningsCount > 0) 'попереджень: ${e.warningsCount}',
            e.uploadedAt,
          ].join('  ·  '),
        ),
        trailing: IconButton(
          icon: const Icon(Icons.delete_outline),
          tooltip: 'Видалити',
          onPressed: () => _deleteEntry(e.id),
        ),
      ),
    );
  }
}

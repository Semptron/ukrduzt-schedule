import 'package:flutter/material.dart';
import 'api_config.dart' show kApiBaseUrl;
import 'schedule_screen.dart';
import 'teacher_search_screen.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const UkrduztCorrespondenceApp());
}

class UkrduztCorrespondenceApp extends StatelessWidget {
  const UkrduztCorrespondenceApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'УкрДУЗТ \u2022 Заочний розклад',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF1565C0),
          brightness: Brightness.light,
        ),
        useMaterial3: true,
      ),
      darkTheme: ThemeData(
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF64B5F6),
          brightness: Brightness.dark,
        ),
        useMaterial3: true,
      ),
      themeMode: ThemeMode.system,
      home: const _MainShell(apiBaseUrl: kApiBaseUrl),
    );
  }
}

class _MainShell extends StatefulWidget {
  final String apiBaseUrl;

  const _MainShell({required this.apiBaseUrl});

  @override
  State<_MainShell> createState() => _MainShellState();
}

class _MainShellState extends State<_MainShell> {
  int _index = 0;

  @override
  Widget build(BuildContext context) {
    final pages = [
      ScheduleScreen(apiBaseUrl: widget.apiBaseUrl),
      TeacherSearchScreen(
        apiBaseUrl: widget.apiBaseUrl,
        isActive: _index == 1,
      ),
    ];

    return Scaffold(
      body: IndexedStack(index: _index, children: pages),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _index,
        onDestinationSelected: (idx) => setState(() => _index = idx),
        destinations: const [
          NavigationDestination(
            icon: Icon(Icons.calendar_today_outlined),
            selectedIcon: Icon(Icons.calendar_today),
            label: 'Розклад',
          ),
          NavigationDestination(
            icon: Icon(Icons.person_search_outlined),
            selectedIcon: Icon(Icons.person_search),
            label: 'Викладачі',
          ),
        ],
      ),
    );
  }
}

import 'package:http/http.dart' as http;
import 'dart:convert';

class ApiService {
  static const String baseUrl = 'http://10.0.2.2:5000/api'; // .NET Core API

  Future<Map<String, dynamic>> fetchDashboardData() async {
    // This is a placeholder for the future connection to the ASP.NET Core API.
    // Replace with actual endpoint and headers when ready.
    try {
      final response = await http.get(Uri.parse('$baseUrl/MobileDashboard/Stats'));
      if (response.statusCode == 200) {
        return json.decode(response.body);
      } else {
        throw Exception('Failed to load dashboard data');
      }
    } catch (e) {
      throw Exception('Network error: $e');
    }
  }
}

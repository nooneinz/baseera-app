from django.urls import path
from . import views
from . import api_views

urlpatterns = [
    path("", views.welcome, name="welcome"),
    path("login/", views.user_login, name="login"),
    path("password-reset/", views.password_reset, name="password_reset"),
    path("register/", views.user_register, name="register"),
    path("logout/", views.user_logout, name="logout"),
    path("portal/", views.portal, name="portal"),
    path("dashboard/", views.dashboard, name="dashboard"),
    
    path("datasets/", views.datasets, name="datasets"),
    path("documents/", views.datasets, name="documents"),

    # Impersonation and dataset actions
    path("impersonate/<int:user_id>/", views.impersonate_user, name="impersonate_user"),
    path("stop-impersonate/", views.stop_impersonate, name="stop_impersonate"),
    path("datasets/delete/<int:file_id>/", views.delete_dataset, name="delete_dataset"),
    path("export-excel/", views.export_excel_report, name="export_excel_report"),
    path("export-note/", views.export_note_report, name="export_note_report"),

    path("reports/", views.reports, name="reports"),
    path("notifications/", views.notifications_view, name="notifications"),
    path("chat-history/", views.chat_history_view, name="chat_history"),
    path("settings/", views.user_settings, name="settings"),
    path("settings/strategic-profile/", views.strategic_profile, name="strategic_profile"),
    # Add newly discovered missing links
    path("templates-feedback/", views.templates_feedback, name="templates_feedback"),
    path("pricing/", views.pricing, name="pricing"),
    path("process-payment/", views.process_payment, name="process_payment"),
    path("connect-live-web/", views.connect_live_web, name="connect_live_web"),
    path("save-manual-note/", views.save_manual_note, name="save_manual_note"),
    
    # Admin links
    path("admin-login/", views.admin_login, name="admin_login"),
    path("admin-dashboard/", views.admin_dashboard, name="admin_dashboard"),
    path("admin-settings/", views.admin_settings, name="admin_settings"),
    path("admin-logs/", views.admin_logs, name="admin_logs"),
    path("admin-finance/", views.admin_finance, name="admin_finance"),


    path("contact/", views.contact, name="contact"),
    path("about/", views.about, name="about"),
    path("privacy/", views.privacy, name="privacy"),
    path("terms/", views.terms, name="terms"),
    path("agents/", views.agents_hub, name="agents_hub"),
    path("all-agents/", views.all_agents_gallery, name="all_agents_gallery"),
    path("boardroom/", views.boardroom_view, name="boardroom"),
    path("agents-workspace/", views.agents_workspace, name="agents_workspace"),
    path("ask-basira/", views.ask_basira, name="ask_basira"),
    path("social-login/<str:provider>/", views.social_login_dummy, name="social_login"),

    
    # API endpoints
    path("api/insights/chat", views.chat_api, name="chat_api"),
    path("api/ai/health/", views.api_ai_health, name="api_ai_health"),
    path("api/documents/auto-save/", views.api_auto_save_document, name="api_auto_save_document"),
    path("api/record-ai-usage/", views.record_ai_usage, name="record_ai_usage"),
    path("api/committee/save-thread/", views.api_committee_save_thread, name="api_committee_save_thread"),
    path("api/committee/get-threads/", views.api_committee_get_threads, name="api_committee_get_threads"),
    path("api/dynamic-chat/", views.api_dynamic_chat, name="api_dynamic_chat"),
    path("api/custom-agents/create/", views.api_create_custom_agent, name="api_create_custom_agent"),
    path("api/custom-agents/delete/<int:agent_id>/", views.api_delete_custom_agent, name="api_delete_custom_agent"),
    path("api/boardroom/debate/", views.api_boardroom_debate, name="api_boardroom_debate"),
    path("api/goals/update/", views.api_update_sales_goal, name="api_update_sales_goal"),
    path("api/analyze-waste/", views.api_analyze_waste, name="api_analyze_waste"),
    path("api/escalation-chain/", views.api_escalation_chain, name="api_escalation_chain"),
    path("api/notifications/mark-read/", views.api_mark_notifications_read, name="api_mark_notifications_read"),
    path("api/notifications/delete/<int:notif_id>/", views.api_delete_notification, name="api_delete_notification"),
    path("api/anomalies/dismiss/<int:alert_id>/", views.api_dismiss_anomaly, name="api_dismiss_anomaly"),
    path("api/weekly-digest/", views.api_get_weekly_digest, name="api_get_weekly_digest"),
    path("api/dashboard/apply-agent-decision/", views.api_apply_agent_decision, name="api_apply_agent_decision"),

    path("api/receipt/save/", views.save_receipt_record, name="save_receipt_record"),

    path("api/save_file/", api_views.save_file_api, name="save_file_api"),
    path("api/live-sync/", api_views.live_sync_api, name="live_sync_api"),
    path("api/mobile/login", api_views.mobile_login, name="mobile_login"),
    path("api/mobile/register", api_views.mobile_register, name="mobile_register"),
    path("api/mobile/change-password", api_views.mobile_change_password, name="mobile_change_password"),
    path("api/mobile/forgot-password", api_views.mobile_forgot_password, name="mobile_forgot_password"),
    path("api/mobile/verify-otp", api_views.mobile_verify_otp, name="mobile_verify_otp"),
    path("api/mobile/upload", api_views.mobile_upload, name="mobile_upload"),
    path("api/mobile/connect-live", api_views.mobile_connect_live, name="mobile_connect_live"),
    path("api/mobile/toggle-user-status", api_views.mobile_toggle_user_status, name="mobile_toggle_user_status"),
    path("api/mobile/dashboard", api_views.mobile_dashboard_data, name="mobile_dashboard_data"),
    path("api/mobile/extract-receipt", api_views.extract_receipt_api, name="extract_receipt_api"),
    path("api/mobile/chat-history", api_views.api_mobile_chat_history, name="api_mobile_chat_history"),
    path("api/mobile/export-excel", api_views.api_mobile_export_excel, name="api_mobile_export_excel"),
    path("api/mobile/export-note", api_views.api_mobile_export_note, name="api_mobile_export_note"),

path("api/dashboard/charts-engine/", api_views.charts_engine_api, name="charts_engine_api"),
    path("api/approved-plans/<int:plan_id>/update-note/", api_views.update_plan_note_api, name="update_plan_note_api"),
path("api/approved-plans/<int:plan_id>/download/", api_views.download_plan_api, name="download_plan_api"),
    path("api/approved-plans/<int:plan_id>/delete/", api_views.delete_plan_api, name="delete_plan_api"),
    path("api/approved-plans/<int:plan_id>/record-impact/", api_views.record_plan_impact_api, name="record_plan_impact_api"),
]

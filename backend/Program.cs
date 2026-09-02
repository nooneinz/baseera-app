using Microsoft.AspNetCore.Mvc;
using System.Text.Json;
using System.Net.Http.Json;
using Microsoft.EntityFrameworkCore;
using BaseeraAPI.Data;
using BaseeraAPI.Services;
using Microsoft.AspNetCore.RateLimiting;
using System.Threading.RateLimiting;
using Serilog;

Log.Logger = new LoggerConfiguration()
    .WriteTo.Console()
    .WriteTo.File("logs/baseera-api-.txt", rollingInterval: RollingInterval.Day)
    .CreateLogger();

var builder = WebApplication.CreateBuilder(args);
builder.Host.UseSerilog();

builder.WebHost.UseSentry(o =>
{
    o.Dsn = builder.Configuration["Sentry:Dsn"] ?? "";
    o.Debug = builder.Environment.IsDevelopment();
    o.TracesSampleRate = 1.0;
});

// Add services to the container.
builder.Services.AddControllers();
builder.Services.AddEndpointsApiExplorer();
builder.Services.AddSwaggerGen();
builder.Services.AddHttpClient(); // Added for n8n integration

// Add Redis Caching
builder.Services.AddStackExchangeRedisCache(options =>
{
    options.Configuration = builder.Configuration.GetConnectionString("Redis") ?? "localhost:6379";
    options.InstanceName = "BaseeraAPI_";
});
builder.Services.AddOutputCache();

// Add Rate Limiting
builder.Services.AddRateLimiter(options =>
{
    options.GlobalLimiter = PartitionedRateLimiter.Create<HttpContext, string>(httpContext =>
        RateLimitPartition.GetFixedWindowLimiter(
            partitionKey: httpContext.User.Identity?.Name ?? httpContext.Request.Headers.Host.ToString(),
            factory: partition => new FixedWindowRateLimiterOptions
            {
                AutoReplenishment = true,
                PermitLimit = 100,
                QueueLimit = 0,
                Window = TimeSpan.FromMinutes(1)
            }));
    options.RejectionStatusCode = StatusCodes.Status429TooManyRequests;
});

// Register Database Context and Tenant Service
builder.Services.AddHttpContextAccessor();
builder.Services.AddScoped<ITenantService, TenantService>();

// Connection string resolution, in priority order:
//   1. ConnectionStrings:DefaultConnection -- a full ADO.NET string, for
//      anyone who wants to paste one directly (env var
//      ConnectionStrings__DefaultConnection).
//   2. DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD -- Render's managed
//      Postgres exposes these as separate `fromDatabase` properties
//      (host/port/database/user/password) rather than a single URI the
//      way DATABASE_URL works elsewhere; Npgsql's UseNpgsql() expects the
//      ADO.NET keyword format, not a postgres:// URI, so this assembles
//      it from the separate values instead of hand-parsing a URI.
//   3. The local docker-compose default, unchanged, for local dev.
var connectionString = builder.Configuration.GetConnectionString("DefaultConnection");
if (string.IsNullOrEmpty(connectionString))
{
    var dbHost = builder.Configuration["DB_HOST"];
    if (!string.IsNullOrEmpty(dbHost))
    {
        connectionString =
            $"Host={dbHost};" +
            $"Port={builder.Configuration["DB_PORT"] ?? "5432"};" +
            $"Database={builder.Configuration["DB_NAME"]};" +
            $"Username={builder.Configuration["DB_USER"]};" +
            $"Password={builder.Configuration["DB_PASSWORD"]};" +
            "SSL Mode=Require;Trust Server Certificate=true";
    }
}
connectionString ??= "Host=localhost;Port=5433;Database=baseera_db;Username=baseera_user;Password=baseera_password";

builder.Services.AddDbContext<BaseeraDbContext>(options => options.UseNpgsql(connectionString));

builder.Services.AddLocalization(options => options.ResourcesPath = "Resources");

// Configure CORS for frontend
builder.Services.AddCors(options =>
{
    options.AddPolicy("AllowFrontend",
        policy =>
        {
            policy.WithOrigins(
                "http://localhost:8080", "http://127.0.0.1:8080",
                "http://localhost:8000", "http://127.0.0.1:8000",
                "http://localhost:5173", "http://127.0.0.1:5173"
            )
                  .AllowAnyHeader()
                  .AllowAnyMethod();
        });
});

var app = builder.Build();

if (app.Environment.IsDevelopment())
{
    app.UseSwagger();
    app.UseSwaggerUI();
}

app.UseSerilogRequestLogging();

app.UseExceptionHandler(errorApp =>
{
    errorApp.Run(async context =>
    {
        context.Response.StatusCode = 500;
        context.Response.ContentType = "application/json";
        var error = context.Features.Get<Microsoft.AspNetCore.Diagnostics.IExceptionHandlerFeature>();
        if (error != null)
        {
            Log.Error(error.Error, "Unhandled exception captured in Global Middleware");
            if (app.Environment.IsDevelopment())
            {
                await context.Response.WriteAsJsonAsync(new { Error = "An unexpected error occurred.", Details = error.Error.Message });
            }
            else
            {
                await context.Response.WriteAsJsonAsync(new { Error = "An unexpected error occurred. Please try again later." });
            }
        }
    });
});

app.UseOutputCache();

// app.UseHttpsRedirection();

app.UseCors("AllowFrontend");

app.UseSentryTracing();
app.UseRateLimiter();

var supportedCultures = new[] { "ar-OM", "ar", "en-US", "en" };
var localizationOptions = new RequestLocalizationOptions()
    .SetDefaultCulture("ar")
    .AddSupportedCultures(supportedCultures)
    .AddSupportedUICultures(supportedCultures);
app.UseRequestLocalization(localizationOptions);

app.UseAuthorization();

app.MapControllers();

app.Run();

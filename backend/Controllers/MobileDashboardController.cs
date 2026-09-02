using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.Filters;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;

namespace BaseeraAPI.Controllers
{
    // Custom Action Filter to validate the Team API Key
    public class TeamApiKeyAuthorizeAttribute : ActionFilterAttribute
    {
        private const string API_KEY_HEADER_NAME = "X-Team-API-Key";

        public override void OnActionExecuting(ActionExecutingContext context)
        {
            // Was a literal string in source (committed to git, visible to
            // anyone with repo access) -- now read from configuration, so
            // it comes from the TeamApiKey env var in every real
            // deployment and never lives in source control again.
            var configuration = context.HttpContext.RequestServices.GetRequiredService<IConfiguration>();
            var expectedApiKey = configuration["TeamApiKey"];

            if (string.IsNullOrEmpty(expectedApiKey))
            {
                // Fail closed: an unset key must never be treated as "no
                // auth required" -- that would silently open every
                // endpoint in this controller. A deployment that forgot
                // to set TeamApiKey gets a 500 here, not an open API.
                context.Result = new ObjectResult(new { message = "Server misconfiguration: API key not set." })
                {
                    StatusCode = StatusCodes.Status500InternalServerError,
                };
                return;
            }

            if (!context.HttpContext.Request.Headers.TryGetValue(API_KEY_HEADER_NAME, out var extractedApiKey))
            {
                context.Result = new UnauthorizedObjectResult(new { message = "API Key is missing." });
                return;
            }

            if (!expectedApiKey.Equals(extractedApiKey))
            {
                context.Result = new UnauthorizedObjectResult(new { message = "Invalid API Key." });
                return;
            }

            base.OnActionExecuting(context);
        }
    }

    // NOTE: every endpoint below returns hardcoded placeholder data, not a
    // real query against BaseeraDbContext -- the mobile app that would
    // consume this is parked (not being built right now). Left as-is
    // deliberately rather than wired to real data, since there is no
    // sales/orders entity in Domain/Entities.cs for it to query yet, and
    // that data model doesn't exist until the mobile app work resumes.
    [ApiController]
    [Route("api/dashboard")]
    [TeamApiKeyAuthorize] // Secures all endpoints in this controller
    public class MobileDashboardController : ControllerBase
    {
        [HttpGet("stats")]
        public IActionResult GetStats()
        {
            var stats = new
            {
                daily_revenue = 8450,
                active_orders = 32,
                canceled_orders = 3,
                top_selling_item = "Iced Americano"
            };

            return Ok(stats);
        }

        [HttpGet("charts")]
        public IActionResult GetCharts()
        {
            var charts = new[]
            {
                new { time = "08:00 AM", sales = 150 },
                new { time = "09:00 AM", sales = 320 },
                new { time = "10:00 AM", sales = 500 },
                new { time = "11:00 AM", sales = 420 },
                new { time = "12:00 PM", sales = 800 }
            };

            return Ok(charts);
        }

        [HttpGet("ai-recommendations")]
        public IActionResult GetAiRecommendations()
        {
            var insight = new
            {
                status = "success",
                ai_insight = "مبيعات اللاتيه تنخفض في المساء، نقترح إطلاق عرض ترويجي.",
                action_required = "Create Promo"
            };

            return Ok(insight);
        }
    }
}

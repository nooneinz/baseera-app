# Multi-stage build: the SDK image (large, has the compiler) only exists
# to produce the published output; the runtime image that actually ships
# is the much smaller ASP.NET runtime-only image.
FROM mcr.microsoft.com/dotnet/sdk:8.0 AS build
WORKDIR /src

COPY BaseeraAPI.csproj .
RUN dotnet restore BaseeraAPI.csproj

COPY . .
RUN dotnet publish BaseeraAPI.csproj -c Release -o /app/publish --no-restore

FROM mcr.microsoft.com/dotnet/aspnet:8.0 AS runtime
WORKDIR /app
COPY --from=build /app/publish .

# Non-root, consistent with sandbox/Dockerfile's reasoning: no service in
# this stack should run as root by default.
RUN useradd --create-home --uid 1000 baseera
USER baseera

ENV ASPNETCORE_URLS=http://+:8080
EXPOSE 8080
ENTRYPOINT ["dotnet", "BaseeraAPI.dll"]

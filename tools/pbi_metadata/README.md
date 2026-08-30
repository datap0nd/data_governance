# Power BI metadata helper

This small .NET helper reads a semantic model through the Power BI XMLA
endpoint and returns only model metadata as JSON. It receives the existing
short-lived delegated access token over standard input; the token is never
written to disk or included in output. The token is assigned through the
Analysis Services `AccessToken` API, rather than being treated as a password.

Production releases include a tested Windows x64 build targeting the .NET
Framework already present on supported Windows 10/11 installations. Setup
extracts that build without contacting NuGet or requiring the separate .NET 8
runtime. A local SDK build is retained only as a fallback. If XMLA is not
available for the workspace, Metronome uses Fabric `getDefinition` instead.

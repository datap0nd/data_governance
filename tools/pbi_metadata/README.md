# Power BI metadata helper

This small .NET helper reads a semantic model through the Power BI XMLA
endpoint and returns only model metadata as JSON. It receives the existing
short-lived delegated access token over standard input; the token is never
written to disk or included in output.

`setup.ps1` publishes it when a .NET 8 SDK is available. If it is unavailable
or the workspace has no XMLA capability, Metronome uses Fabric
`getDefinition` instead.

using System.Text.Json;
using Microsoft.AnalysisServices.Tabular;
using Json = System.Text.Json.JsonSerializer;

record Request(string Workspace, string DatasetId, string? DatasetName, string AccessToken);
record PartitionDto(string Name, string? Mode, string? Expression);
record MeasureDto(string Name, string? Expression);
record TableDto(string Name, string[] Columns, MeasureDto[] Measures, PartitionDto[] Partitions);
record ResponseDto(TableDto[] Tables, Dictionary<string, string> Expressions, string? Error = null);

static class Program
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    public static int Main()
    {
        try
        {
            var request = Json.Deserialize<Request>(Console.In.ReadToEnd(), JsonOptions)
                ?? throw new InvalidOperationException("Missing JSON request.");
            if (string.IsNullOrWhiteSpace(request.Workspace) ||
                string.IsNullOrWhiteSpace(request.DatasetId) ||
                string.IsNullOrWhiteSpace(request.AccessToken))
            {
                throw new InvalidOperationException("Workspace, datasetId, and accessToken are required.");
            }

            var workspace = Uri.EscapeDataString(request.Workspace.Trim());
            var connectionString =
                $"DataSource=powerbi://api.powerbi.com/v1.0/myorg/{workspace};" +
                $"Password={request.AccessToken};";
            using var server = new Server();
            server.Connect(connectionString);
            var database = server.Databases.Cast<Database>().FirstOrDefault(item =>
                    string.Equals(item.ID, request.DatasetId, StringComparison.OrdinalIgnoreCase))
                ?? server.Databases.Cast<Database>().FirstOrDefault(item =>
                    !string.IsNullOrWhiteSpace(request.DatasetName) &&
                    string.Equals(item.Name, request.DatasetName, StringComparison.OrdinalIgnoreCase))
                ?? throw new InvalidOperationException("The requested semantic model was not visible through XMLA.");

            var tables = database.Model.Tables.Select(table => new TableDto(
                table.Name,
                table.Columns.Select(column => column.Name).ToArray(),
                table.Measures.Select(measure => new MeasureDto(measure.Name, measure.Expression)).ToArray(),
                table.Partitions.Select(partition => new PartitionDto(
                    partition.Name,
                    partition.Mode.ToString(),
                    partition.Source is MPartitionSource source ? source.Expression : null
                )).ToArray()
            )).ToArray();
            var expressions = database.Model.Expressions.ToDictionary(
                expression => expression.Name,
                expression => expression.Expression,
                StringComparer.Ordinal
            );
            Console.Out.Write(Json.Serialize(new ResponseDto(tables, expressions), JsonOptions));
            return 0;
        }
        catch (Exception exception)
        {
            var message = exception.Message;
            Console.Error.Write(message.Length > 1000 ? message[..1000] : message);
            return 1;
        }
    }
}

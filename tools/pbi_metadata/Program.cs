using System;
using System.Collections.Generic;
using System.Linq;
using System.Web.Script.Serialization;
using Microsoft.AnalysisServices.Tabular;

sealed class Request
{
    public string workspace { get; set; } = string.Empty;
    public string datasetId { get; set; } = string.Empty;
    public string? datasetName { get; set; }
    public string accessToken { get; set; } = string.Empty;
}

sealed class PartitionDto
{
    public PartitionDto(string name, string? mode, string? expression)
    {
        this.name = name;
        this.mode = mode;
        this.expression = expression;
    }

    public string name { get; }
    public string? mode { get; }
    public string? expression { get; }
}

sealed class MeasureDto
{
    public MeasureDto(string name, string? expression)
    {
        this.name = name;
        this.expression = expression;
    }

    public string name { get; }
    public string? expression { get; }
}

sealed class TableDto
{
    public TableDto(
        string name,
        string[] columns,
        MeasureDto[] measures,
        PartitionDto[] partitions)
    {
        this.name = name;
        this.columns = columns;
        this.measures = measures;
        this.partitions = partitions;
    }

    public string name { get; }
    public string[] columns { get; }
    public MeasureDto[] measures { get; }
    public PartitionDto[] partitions { get; }
}

sealed class ResponseDto
{
    public ResponseDto(TableDto[] tables, Dictionary<string, string> expressions)
    {
        this.tables = tables;
        this.expressions = expressions;
    }

    public TableDto[] tables { get; }
    public Dictionary<string, string> expressions { get; }
}

static class Program
{
    public static int Main()
    {
        try
        {
            var json = new JavaScriptSerializer { MaxJsonLength = int.MaxValue };
            var request = json.Deserialize<Request>(Console.In.ReadToEnd())
                ?? throw new InvalidOperationException("Missing JSON request.");
            if (string.IsNullOrWhiteSpace(request.workspace) ||
                string.IsNullOrWhiteSpace(request.datasetId) ||
                string.IsNullOrWhiteSpace(request.accessToken))
            {
                throw new InvalidOperationException("Workspace, datasetId, and accessToken are required.");
            }

            var workspace = Uri.EscapeDataString(request.workspace.Trim());
            var connectionString =
                $"DataSource=powerbi://api.powerbi.com/v1.0/myorg/{workspace};" +
                $"Password={request.accessToken};";
            using (var server = new Server())
            {
                server.Connect(connectionString);
                var database = server.Databases.Cast<Database>().FirstOrDefault(item =>
                        string.Equals(item.ID, request.datasetId, StringComparison.OrdinalIgnoreCase))
                    ?? server.Databases.Cast<Database>().FirstOrDefault(item =>
                        !string.IsNullOrWhiteSpace(request.datasetName) &&
                        string.Equals(item.Name, request.datasetName, StringComparison.OrdinalIgnoreCase))
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
                Console.Out.Write(json.Serialize(new ResponseDto(tables, expressions)));
            }
            return 0;
        }
        catch (Exception exception)
        {
            var message = exception.Message;
            Console.Error.Write(message.Length > 1000 ? message.Substring(0, 1000) : message);
            return 1;
        }
    }
}

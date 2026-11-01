using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

// No Python or ML packages are embedded in this Windows bootstrapper.
public static class Launcher
{
    public static string Quote(string value)
    {
        var result = new StringBuilder("\"");
        int slashes = 0;
        foreach (char character in value)
        {
            if (character == '\\') { slashes++; continue; }
            result.Append('\\', character == '"' ? slashes * 2 + 1 : slashes);
            result.Append(character);
            slashes = 0;
        }
        result.Append('\\', slashes * 2);
        return result.Append('"').ToString();
    }

    public static string ConsolePython(string python)
    {
        return Path.GetFileName(python).Equals("pythonw.exe", StringComparison.OrdinalIgnoreCase)
            ? Path.Combine(Path.GetDirectoryName(python), "python.exe") : python;
    }

    public static ProcessStartInfo StartInfo(string root, string python, string[] files)
    {
        var start = Command(root, python, new[] { "-B", Path.Combine(root, "gui.pyw") }.Concat(files).ToArray());
        string windowed = Path.Combine(Path.GetDirectoryName(python), "pythonw.exe");
        if (File.Exists(windowed)) start.FileName = windowed;
        return start;
    }

    public static ProcessStartInfo Command(string root, string python, string[] arguments)
    {
        var start = new ProcessStartInfo(ConsolePython(python), String.Join(" ", arguments.Select(Quote)));
        start.UseShellExecute = false;
        start.CreateNoWindow = true;
        start.WorkingDirectory = root;
        start.StandardOutputEncoding = Encoding.UTF8;
        start.StandardErrorEncoding = Encoding.UTF8;
        // StandardOutputEncoding is used only for redirected commands.
        start.RedirectStandardOutput = true;
        start.RedirectStandardError = true;
        string home = Path.GetDirectoryName(python);
        if (!String.IsNullOrEmpty(home))
            start.EnvironmentVariables["PATH"] = String.Join(";", new[] {
                home, Path.Combine(home, "Scripts"), Path.Combine(home, "Library", "bin"),
                start.EnvironmentVariables["PATH"]
            });
        start.EnvironmentVariables["PYTHONUTF8"] = "1";
        start.EnvironmentVariables["QT_API"] = "pyside6";
        start.EnvironmentVariables.Remove("PYTHONHOME");
        start.EnvironmentVariables.Remove("PYTHONPATH");
        return start;
    }

    public static string Runtime(string root) { return Path.Combine(root, "src", "vbuild", "runtime.py"); }

    public static Dictionary<string, object> Probe(string root, string python)
    {
        using (var process = Process.Start(Command(root, python, new[] { "-B", Runtime(root), "probe" })))
        {
            var output = process.StandardOutput.ReadToEndAsync();
            var error = process.StandardError.ReadToEndAsync();
            if (!process.WaitForExit(20000)) { process.Kill(); throw new IOException("Python 检测超时：" + python); }
            if (process.ExitCode != 0) throw new IOException(error.Result);
            return new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(output.Result);
        }
    }

    [STAThread]
    public static void Main(string[] args)
    {
        Application.EnableVisualStyles();
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string config = Path.Combine(root, "work", "python.txt");
        bool choose = args.Contains("--python") || (Control.ModifierKeys & Keys.Shift) != 0;
        try
        {
            string saved = File.Exists(config) ? File.ReadAllText(config, Encoding.UTF8).Trim() : "";
            string selected = "";
            if (args.Contains("--diagnose"))
            {
                var candidates = File.Exists(saved) ? new[] { saved } : new string[0];
                var report = candidates.Select(path => new { python = path, info = Probe(root, path) }).ToArray();
                string directory = Path.Combine(root, "work", "log");
                Directory.CreateDirectory(directory);
                File.WriteAllText(Path.Combine(directory, "launcher.json"), new JavaScriptSerializer().Serialize(report), new UTF8Encoding(false));
                return;
            }
            if (!choose && saved.Length > 0 && File.Exists(saved))
                selected = ConsolePython(saved); // The application checks dependencies once, before opening Qt.
            if (selected.Length == 0)
                using (var setup = new Setup(root, saved))
                {
                    if (setup.ShowDialog() != DialogResult.OK) return;
                    selected = setup.Selected;
                }
            Directory.CreateDirectory(Path.GetDirectoryName(config));
            if (!selected.Equals(saved, StringComparison.OrdinalIgnoreCase))
                File.WriteAllText(Path.Combine(root, "work", "runtime.json"), "{\"engines\":{}}\n", new UTF8Encoding(false));
            File.WriteAllText(config, selected + Environment.NewLine, new UTF8Encoding(false));
            var start = StartInfo(root, selected, args.Where(value => value != "--python").ToArray());
            // The GUI owns persistent logging; do not leave unread redirected pipes behind.
            start.RedirectStandardOutput = start.RedirectStandardError = false;
            start.StandardOutputEncoding = start.StandardErrorEncoding = null;
            Process.Start(start);
        }
        catch (IOException error) { Error(error.Message); }
        catch (UnauthorizedAccessException error) { Error(error.Message); }
        catch (Win32Exception error) { Error(error.Message); }
    }

    static void Error(string message)
    {
        MessageBox.Show(message + "\n\n按住 Shift 双击 VBuild.exe 可进入 Python 设置。",
                        "VBuild 启动失败", MessageBoxButtons.OK, MessageBoxIcon.Error);
    }
}

public sealed class Setup : Form
{
    readonly string root;
    readonly TextBox python = new TextBox();
    readonly Label status = new Label();
    readonly Button ready = new Button(), browse = new Button();
    bool working;
    public string Selected { get; private set; }

    public Setup(string directory, string saved)
    {
        root = directory;
        Text = "VBuild · Python";
        Icon = new Icon(Path.Combine(root, "src", "vbuild", "icons", "app.ico"));
        ClientSize = new Size(620, 180);
        MinimumSize = new Size(540, 220);
        Font = new Font("Microsoft YaHei UI", 9);
        var layout = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 1, RowCount = 4, Padding = new Padding(14) };
        Controls.Add(layout);
        layout.Controls.Add(new Label { Text = "Python 3.11–3.13（64 位），推荐 3.12。", AutoSize = true });
        python.Dock = DockStyle.Top;
        python.Text = saved;
        layout.Controls.Add(python);
        var buttons = new FlowLayoutPanel { Dock = DockStyle.Top, AutoSize = true };
        ready.Text = "检测并使用"; ready.AutoSize = true; ready.Click += UseExisting;
        browse.Text = "选择 Python…"; browse.AutoSize = true;
        browse.Click += delegate {
            using (var dialog = new OpenFileDialog { Title = "选择 Python", Filter = "Python|python.exe;pythonw.exe" })
                if (dialog.ShowDialog() == DialogResult.OK) python.Text = Launcher.ConsolePython(dialog.FileName);
        };
        buttons.Controls.AddRange(new Control[] { browse, ready });
        layout.Controls.Add(buttons);
        status.Text = "请选择 python.exe。";
        status.Dock = DockStyle.Fill;
        layout.Controls.Add(status);
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        FormClosing += delegate(object sender, FormClosingEventArgs e) { e.Cancel = working; };
    }

    void UseExisting(object sender, EventArgs args)
    {
        string selected = Launcher.ConsolePython(python.Text.Trim());
        if (!File.Exists(selected)) { status.Text = "请选择有效的 python.exe。"; return; }
        working = true;
        python.Enabled = ready.Enabled = browse.Enabled = false;
        status.Text = "正在检测…";
        Task.Run(delegate {
            try
            {
                var details = Launcher.Probe(root, selected);
                BeginInvoke((Action)delegate {
                    working = false;
                    python.Enabled = ready.Enabled = browse.Enabled = true;
                    if (!(bool)details["supported"]) { status.Text = "请选择 Python 3.11–3.13（64 位）。"; return; }
                    // The stdlib startup shows the exact dependency list and asks before installing.
                    Selected = selected; DialogResult = DialogResult.OK; Close();
                });
            }
            catch (Exception error) { BeginInvoke((Action)delegate {
                working = false; python.Enabled = ready.Enabled = browse.Enabled = true; status.Text = error.Message;
            }); }
        });
    }
}

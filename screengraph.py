#!/usr/bin/python

# ---------------------------------------------------------------------
# Be sure to add the python path that points to the LLDB shared library.
#
# # To use this in the embedded python interpreter using "lldb" just
# import it with the full path using the "command script import"
# command
#   (lldb) command script import /path/to/screengraph.py
# ---------------------------------------------------------------------

from __future__ import print_function

import collections
import inspect
import lldb
import optparse
import os
import sets
import shlex
import sys
import textwrap


def debug():
    return False

def debug_print(s):
    if debug():
        print(s)

def make_directory_if_not_exist(directory):
    try:
        os.makedirs(directory)
    except OSError, e:
        if e.errno != os.errno.EEXIST:
            raise

def first_argument():
    arch = lldb.debugger.GetSelectedTarget().GetTriple().split('-')[0]
    if 'x86' in arch:
        first_arg = '(id)$rdx'
    elif 'arm' in arch:
        #TODO test 1st arg on arm architecture
        first_arg = '(id)$r3'
    return first_arg


class singleton:
    
    def __init__(self, klass):
        self.klass = klass
        self.instance = None
        
    def __call__(self, *args, **kwds):
        if self.instance == None:
            self.instance = self.klass(*args, **kwds)
        return self.instance


#-- Outputs

class Output:
    
    def process(self, state):
        raise NotImplementedError


class TextOutput(Output):
    
    def __init__(self, directory):
        self.filename = os.path.join(directory, 'trace.txt')
        
    def process(self, state):
        with open(self.filename, 'a+') as f:
            f.write(str(state) + '\n')


class ScreenshotOutput(Output):
    
    def __init__(self, directory, debugger, on_touch, on_breakpoint):
        self.directory = directory
        self.debugger = debugger
        self.on_touch = on_touch
        self.on_breakpoint = on_breakpoint
        
        self.setup_touch_highlighting(debugger)
        self.setup_screenshot(debugger)
    
    def filename(self, state):
        return os.path.join(self.directory, 'screenshot_%s.png' % state.identifier)
    
    def setup_touch_highlighting(self, debugger):
        frame = self.debugger.GetSelectedTarget().GetProcess().GetSelectedThread().GetSelectedFrame()
        options = lldb.SBExpressionOptions()
        options.SetLanguage(lldb.eLanguageTypeSwift)
        expr = """
            import UIKit
            extension UIWindow {
                public func highlight(_ point: CGPoint) {
                    let circleView = UIView(frame: CGRect(x: 0, y: 0, width: %(size)i, height: %(size)i))
                    circleView.center = point
                    circleView.alpha = 0.5
                    circleView.layer.cornerRadius = %(size)i/2
                    circleView.backgroundColor = UIColor.%(color)s
                    circleView.isUserInteractionEnabled = false
                    self.addSubview(circleView)
                    UIView.animate(withDuration: %(duration)f, delay: 0.0, options: [], animations: {
                        circleView.alpha = 0.0
                    }, completion: { (finished: Bool) in
                        circleView.removeFromSuperview()
                    })
                }
            }
        """ % {
            'size': 40, 
            'color': 'red', 
            'duration': 0.75
        }
        frame.EvaluateExpression(expr, options)
        
    def setup_screenshot(self, debugger):
        frame = self.debugger.GetSelectedTarget().GetProcess().GetSelectedThread().GetSelectedFrame()
        options = lldb.SBExpressionOptions()
        options.SetLanguage(lldb.eLanguageTypeSwift)
        expr = """
            import UIKit
            extension UIWindow {
                public func screenshot(_ path: String) {
                    let view = self.screen.snapshotView(afterScreenUpdates: true)
                    UIGraphicsBeginImageContext(view.bounds.size)
                    view.drawHierarchy(in: view.bounds, afterScreenUpdates: true)
                    if let image = UIGraphicsGetImageFromCurrentImageContext(),
                        let data = image.pngData() {
                        if let fileURL = URL(string: path) {
                            do {
                                try data.write(to: fileURL)
                                print("Screenshot saved")
                            } catch {
                                print("Error saving screenshot:", error)
                            }
                        } else {
                            print("Screengraph could not get valid file path")
                        }
                    } else {
                        print("Screengraph could not take a screenshot")
                    }
                    UIGraphicsEndImageContext()
                }
            }
        """
        frame.EvaluateExpression(expr, options)
        
    def process(self, state):
        if (self.on_breakpoint and isinstance(state, BreakpointState)) \
            or (self.on_touch and isinstance(state, TouchState)):
            self.screenshot(state)
        
    def screenshot(self, state):
        highlight = ('window.highlight(CGPoint(x: %i, y: %i))' % (state.x, state.y)) if \
            isinstance(state, TouchState) else ''
        
        options = lldb.SBExpressionOptions()
        options.SetLanguage(lldb.eLanguageTypeSwift)
        expr = """
            import UIKit
            if let window = UIApplication.shared.keyWindow {
                %(highlight)s
                window.screenshot("file://%(filename)s")
            } else {
                print("Screengraph could not get a window")
            }
        """ % {
            'filename': self.filename(state),
            'highlight': highlight
        }
        state.frame.EvaluateExpression(expr, options)


class GraphvizOutput(Output):
    
    class Edge:
        def __init__(self, src, dst, labelpos=None):
            self.src = src
            self.dst = dst
            self.labelpos = labelpos
            
            self.label = textwrap.fill(str(self.src.state).replace('"', '\\"')) if self.labelpos == 'edge' else ''
        
        def __str__(self):
            return '\n\tN%s -> N%s [label="%s"];' % (
                self.src.state.identifier, 
                self.dst.state.identifier,
                self.label,
            )

    class Node:
        def __init__(self, state, labelpos=None):
            self.state = state
            self.labelpos = labelpos
            self.edges = []
            
            self.label = textwrap.fill(str(self.state).replace('"', '\\"')) if self.labelpos == 'node' else ''
            self.image_filename = ('screenshot_%s.png' % self.state.identifier) if isinstance(self.state, TouchState) else ''

        def add_edge(self, node, labelpos=None):
            if not node in [edge.dst for edge in self.edges]:
                self.edges.append(GraphvizOutput.Edge(self, node, labelpos or self.labelpos))
            
        def __str__(self):
            return '\n\tN%s [image="%s", label="%s"];' % (
                self.state.identifier,
                self.image_filename,
                self.label,
            )

    def __init__(self, directory, reentry, labelpos='node'):
        self.filename = os.path.join(directory, 'graph.dot')
        self.reentry = reentry
        self.labelpos = labelpos # 'edge' or 'node' or None
        
        self.root = None
        self.last = None
        
        self.nodes = collections.OrderedDict()
        
    def process(self, state):
        
        if self.reentry:
            if isinstance(state, TouchState):
                key = self.visible_view_controller(state.frame)
            else:
                key = str(state.frame)
                
            node = GraphvizOutput.Node(state, labelpos=self.labelpos)
            if key not in self.nodes.keys():
                self.nodes[key] = []
            self.nodes[key].append(node)

        else: # no reentry
            node = GraphvizOutput.Node(state, labelpos=self.labelpos)

        if not self.root:
            self.root = node
        
        if self.last:
            if self.last == node: # edge loops on same node
                self.last.add_edge(node, labelpos='edge')
            else:
                self.last.add_edge(node)
        
        self.last = node
        
        with open(self.filename, 'w+') as f:
            f.write(self.output)
        
        #TODO generate png from graph automatically

    def visible_view_controller(self, frame):
        options = lldb.SBExpressionOptions()
        options.SetLanguage(lldb.eLanguageTypeSwift)
        value = frame.EvaluateExpression('''
            import UIKit
            func getVisibleViewController(_ rootViewController: UIViewController?) -> UIViewController? {
                let viewController = rootViewController ?? UIApplication.shared.keyWindow?.rootViewController
                if let presented = viewController?.presentedViewController {
                    return getVisibleViewController(presented)
                }
                if let navigationController = viewController as? UINavigationController {
                    return getVisibleViewController(navigationController.viewControllers.last!)
                }
                if let tabBarController = viewController as? UITabBarController {
                    return getVisibleViewController(tabBarController.selectedViewController!)
                }
                return viewController
            }
            let viewController = getVisibleViewController(nil)
            viewController?.title ?? (viewController != nil ? NSStringFromClass(type(of:viewController!)) : "unknown")
        ''', options).GetObjectDescription()
        debug_print('view controller: ' + value)
        return value

    @property
    def output(self): 
        #TODO rewrite this to reduce complexity of node traversal (like https://stackoverflow.com/a/10289740)
        
        queue = [self.root]
        visited = sets.Set()
        
        nodes_text = ''
        edges_text = ''
        
        while queue:
            node = queue.pop(0)
            visited.add(node)
            
            if not self.reentry:
                nodes_text += str(node)
                
            for edge in node.edges:
                if not edge.dst in visited:
                    queue.append(edge.dst)
                    edges_text += str(edge)
        
        if self.reentry:
            subgraph = 0                
            for key, nodes in self.nodes.iteritems():
                n = ''.join([str(node) for node in nodes])
                pairs = list(zip(nodes, nodes[1:]))
                e = ''.join(['N%s -> N%s [style=invis, constraint=false];\n' % (str(s.state.identifier), str(d.state.identifier)) for s, d in pairs])
                nodes_text += '''
                    subgraph cluster_%i {
                        style = filled;
                        color = lightgrey;
                        edge [dir=none];
                        %s
                        %s
                    }
                ''' % (subgraph, n, e)
                subgraph += 1
        
        output = textwrap.dedent('''
            digraph G {
                rankdir = LR;
                node [shape=rect, labelloc=b];
                %s
                %s
            }
        ''') % (nodes_text, edges_text)
        debug_print('GraphvizOutput: ' + output)
        return output


#-- State

class State:
    
    def __str__(self):
        raise NotImplementedError
        
    @property
    def identifier(self):
        raise NotImplementedError
        
    @property
    def frame(self):
        raise NotImplementedError


class BreakpointState(State):
    
    def __init__(self, identifier, frame, location):
        self.identifier = identifier
        self.frame = frame
        self.location = location
        
    def __repr__(self):
        return '<State (%s): breakpoint %s, function %s>' % (
            self.identifier,
            self.location.GetBreakpoint().id,
            self.frame.name,
        )
    
    def __str__(self):
        return str(self.frame)


class TouchState(State):
    
    def __init__(self, identifier, x, y, frame, location):
        self.identifier = identifier
        self.x = x
        self.y = y
        self.frame = frame
        self.location = location
        
    def __repr__(self):
        return '<State (%s): touch (%i, %i)>' % (
            self.identifier,
            self.x,
            self.y,
        )
    
    def __str__(self):
        return 'Touch (%i, %i)' % (
            self.x,
            self.y,
        )


#-- Tracing

class Tracer:
    
    def start(self):
        raise NotImplementedError
    
    def stop(self):
        raise NotImplementedError


@singleton
class BreakpointTracer(Tracer):

    def __init__(self, debugger, outputs):
        self.debugger = debugger
        self.outputs = outputs
        self._current_idx = 0
    
    def __del__(self):
        self.stop()
            
    def start(self):
        debug_print('starting breakpoint tracer')
        self.breakpoints = []
        target = self.debugger.GetSelectedTarget()
        for breakpoint in target.breakpoint_iter():
            if breakpoint.IsValid() and breakpoint.IsEnabled():
                debug_print('breakpoint: ' + str(breakpoint))
                breakpoint.SetScriptCallbackFunction('screengraph.BreakpointTracer.instance.on_breakpoint_hit')
                self.breakpoints.append(breakpoint)
    
    def stop(self):
        debug_print('stopping breakpoint tracer')
        for breakpoint in self.breakpoints:
            if breakpoint.IsValid():
                dir(breakpoint)
                breakpoint.SetScriptCallbackFunction("")
        self.breakpoints = []
        
    def on_breakpoint_hit(self, frame, location, internal_dict):
        if frame.IsValid():
            debug_print('frame: ' + str(frame))
            state = BreakpointState(self.current_identifier, frame, location)
            debug_print('state: ' + repr(state))
            for output in self.outputs:
                output.process(state)
            frame.GetThread().GetProcess().Continue()
    
    @property
    def current_identifier(self):
        ret = self._current_idx
        self._current_idx = self._current_idx + 1
        return 'b' + str(ret)


@singleton
class TouchTracer(Tracer):
    
    def __init__(self, debugger, outputs):
        self.debugger = debugger
        self.outputs = outputs
        self._current_idx = 0
        self.hitTest = None
    
    def start(self):
        debug_print('starting touch tracer')
        target = self.debugger.GetSelectedTarget()

        first_arg = first_argument()
        condition = '(int)[%s type] == 0 && (int)[[[%s allTouches] anyObject] phase] == 0' % (
            first_arg,
            first_arg
        )

        self.hitTest = target.BreakpointCreateByName('-[UIApplication sendEvent:]')
        self.hitTest.SetCondition(condition) 
        self.hitTest.SetScriptCallbackFunction('screengraph.TouchTracer.instance.on_touch')
        
    def stop(self):
        debug_print('stopping touch tracer')
        #TODO implement TouchTracer stop
        raise NotImplementedError 
    
    def on_touch(self, frame, location, internal_dict):
        if frame.IsValid():
            debug_print('touch: ' + str(frame))
            
            first_arg = first_argument()
            value = frame.EvaluateExpression('''
                @import CoreGraphics; 
                UIEvent *event = %s; 
                UITouch *touch = (UITouch *)[[event allTouches] anyObject]; 
                CGPoint point = (CGPoint)[touch locationInView:touch.window]; 
                point
            ''' % first_arg) 
            x = float(value.GetChildMemberWithName('x').GetValue())
            y = float(value.GetChildMemberWithName('y').GetValue())
            
            state = TouchState(self.current_identifier, x, y, frame, location)
            debug_print('state: ' + repr(state))
            
            for output in self.outputs:
                output.process(state)
            frame.GetThread().GetProcess().Continue()
    
    @property
    def current_identifier(self):
        ret = self._current_idx
        self._current_idx = self._current_idx + 1
        return 't' + str(ret)
        

#-- Command

class ScreenGraphCommand:
    program = 'screengraph'

    @classmethod
    def register_lldb_command(cls, debugger, module_name):
        parser = cls.create_options()
        cls.__doc__ = parser.format_help()
        command = 'command script add -c %s.%s %s' % (module_name,
                                                      cls.__name__,
                                                      cls.program)
        debugger.HandleCommand(command)
        print('The "{0}" command has been installed, type "help {0}" or "{0} '
              '--help" for detailed help.'.format(cls.program))

    @classmethod
    def create_options(cls):
        usage = "usage: %prog start|stop"
        description = ('Creates a graph of screens.')
        
        parser = optparse.OptionParser(
            description=description,
            prog=cls.program,
            usage=usage,
        )
        
        parser.add_option(
            "-t", "--type",
            metavar='type',
            default='graph',
            help='Output type: linear or graph (default=graph)',
        )
        
        parser.add_option(
            "-d", "--directory",
            metavar='directory',
            default=os.path.join(os.path.expanduser("~"), 'screengraph'),
            help='Output directory (default=~/screengraph)',
        )
        
        #TODO add command option for graph orientation
        #TODO add command option to disable continuing after hitting a breakpoint
        #TODO add command option to select outputs
        
        return parser
        
    def get_short_help(self):
        return 'Creates a graph of screens.'

    def get_long_help(self):
        return self.self.parser.format_help()

    def __init__(self, debugger, unused):
        self.parser = self.create_options()
        self.tracers = []
        self.tracing = False
        
    def __call__(self, debugger, command, exe_ctx, result):
        command_args = shlex.split(command)
        try:
            (options, args) = self.parser.parse_args(command_args)
        except:
            # result.SetError("Option parsing failed")
            return

        if not args:
            subcommand = 'start'
        else:
            subcommand = args[0]
        
        if subcommand == 'start' and not self.tracing:
            print('starting screengraph')
            outputs = self.make_outputs(
                debugger, 
                options.directory,
                reentry = (options.type == 'graph')
            )

            self.tracers = self.make_tracers(debugger, outputs)
            self.tracing = True
            [tracer.start() for tracer in self.tracers]
            
            #TODO investigate why resuming debugger after starting screengraph does not work
            #debugger.GetSelectedTarget().GetProcess().Continue()
            
        elif subcommand == 'stop' and self.tracing:
            print('stopping screengraph')
            [tracer.stop() for tracer in self.tracers]
            self.tracing = False
            
    def make_tracers(self, debugger, outputs, breakpoint=True, touch=True):
        tracers = []
        if breakpoint:
            tracers.append(BreakpointTracer(debugger, outputs))
        if touch:
            tracers.append(TouchTracer(debugger, outputs))
        return tracers
    
    def make_outputs(self, debugger, directory, text=debug(), screenshot=True, graphviz=True, reentry=True):
        make_directory_if_not_exist(directory)
        outputs = []
        if text:
            outputs.append(TextOutput(directory))
        if screenshot:
            outputs.append(ScreenshotOutput(directory, debugger, on_touch=True, on_breakpoint=False))
        if graphviz:
            outputs.append(GraphvizOutput(directory, reentry=reentry))
        return outputs

def __lldb_init_module(debugger, dict):   
    for _name, cls in inspect.getmembers(sys.modules[__name__]):
        if inspect.isclass(cls) and callable(getattr(cls,
                                                     "register_lldb_command",
                                                     None)):
            cls.register_lldb_command(debugger, __name__)
from dolfin import *
from dolfin_adjoint import *
import libadjoint
import numpy
import sys

class parameters(dict):
    '''Parameter dictionary. This subclasses dict so defaults can be set.'''
    def __init__(self, dict={}):
        self["start_time"] = 0.0
        self["current_time"] = 0.0
        self["theta"] = 0.5
        self["verbose"] = 10

        # Apply dict after defaults so as to overwrite the defaults
        for key,val in dict.iteritems():
            self[key]=val

        self.required={
            "depth" : "water depth",
            "dt" : "timestep",
            "finish_time" : "finish time",
            "dump_period" : "dump period in timesteps",
            "basename" : "base name for I/O"
            }

    def check(self):
        for key, error in self.required.iteritems():
            if not self.has_key(key):
                sys.stderr.write("Missing parameter: "+key+"\n"+
                                 "This is used to set the "+error+"\n")
                raise KeyError

def rt0(mesh):
    "Return a function space U*H on mesh from the rt0 space."

    V = FunctionSpace(mesh, 'Raviart-Thomas', 1) # Velocity space
 
    H = FunctionSpace(mesh, 'DG', 0)             # Height space

    W=V*H                                        # Mixed space of both.

    return W

def p2p1(mesh):
    "Return a function space U*H on mesh from the p2p1 space."

    V = VectorFunctionSpace(mesh, 'CG', 2, dim=2)# Velocity space
 
    H = FunctionSpace(mesh, 'CG', 1)             # Height space

    W=V*H                                        # Mixed space of both.

    return W

def p1dgp2(mesh):
    "Return a function space U*H on mesh from the rt0 space."

    V = VectorFunctionSpace(mesh, 'DG', 1, dim=2)# Velocity space
 
    H = FunctionSpace(mesh, 'CG', 2)             # Height space

    W=V*H                                        # Mixed space of both.

    return W

def bdfmp1dg(mesh):
    "Return a function space U*H on mesh from the BFDM1 space."

    V = FunctionSpace(mesh, 'BDFM', 1)# Velocity space
 
    H = FunctionSpace(mesh, 'DG', 1)             # Height space

    W=V*H                                        # Mixed space of both.

    return W

def bdmp0(mesh):
    "Return a function space U*H on mesh from the BFDM1 space."

    V = FunctionSpace(mesh, 'BDM', 1)# Velocity space
 
    H = FunctionSpace(mesh, 'DG', 0)             # Height space

    W=V*H                                        # Mixed space of both.

    return W

def bdmp1dg(mesh):
    "Return a function space U*H on mesh from the BFDM1 space."

    V = FunctionSpace(mesh, 'BDM', 1)# Velocity space
 
    H = FunctionSpace(mesh, 'DG', 1)             # Height space

    W = V*H                                        # Mixed space of both.

    return W

def save_to_file_scalar(function, basename):
    u_out, p_out = output_files(basename)

    M_p_out, q_out, p_out_func = p_output_projector(function.function_space())

    # Project the solution to P1 for visualisation.
    rhs = assemble(inner(q_out,function)*dx)
    solve(M_p_out, p_out_func.vector(),rhs,"cg","sor", annotate=False) 
    
    p_out << p_out_func

def save_to_file(function, basename):
    u_out,p_out = output_files(basename)

    M_u_out, v_out, u_out_func = u_output_projector(function.function_space())
    M_p_out, q_out, p_out_func = p_output_projector(function.function_space())

    # Project the solution to P1 for visualisation.
    rhs = assemble(inner(v_out,function.split()[0])*dx)
    solve(M_u_out, u_out_func.vector(), rhs, "cg", "sor", annotate=False) 
    
    # Project the solution to P1 for visualisation.
    rhs = assemble(inner(q_out,function.split()[1])*dx)
    solve(M_p_out, p_out_func.vector(), rhs, "cg", "sor", annotate=False) 
    
    u_out << u_out_func
    p_out << p_out_func

def sw_solve(W, config, state, turbine_field=None, time_functional=None, annotate=True, linear_solver="default", preconditioner="default", u_source = None):
    '''Solve the shallow water equations with the parameters specified in params.
       Options for linear_solver and preconditioner are: 
        linear_solver: lu, cholesky, cg, gmres, bicgstab, minres, tfqmr, richardson
        preconditioner: none, ilu, icc, jacobi, bjacobi, sor, amg, additive_schwarz, hypre_amg, hypre_euclid, hypre_parasails, ml_amg
    '''

    ############################### Setting up the equations ###########################

    # Define variables for all used parameters
    ds = config.ds
    params = config.params
    theta = params["theta"]
    dt = params["dt"]
    g = params["g"]
    depth = params["depth"]
    params["current_time"] = params["start_time"]
    t = params["current_time"]
    quadratic_friction = params["quadratic_friction"]
    include_advection = params["include_advection"]
    include_diffusion = params["include_diffusion"]
    diffusion_coef = params["diffusion_coef"]
    newton_solver = params["newton_solver"] 
    picard_iterations = params["picard_iterations"]
    solver_benchmark = params["solver_benchmark"]
    is_nonlinear = (include_advection or quadratic_friction)

    # To begin with, check if the provided parameters are valid
    params.check()

    # Print out an estimation of the Reynolds number 
    print "Expected Reynolds number is roughly (assumes velocity is 2): ",
    if include_diffusion and diffusion_coef>0:
      print params["turbine_x"]*2./diffusion_coef
    else:
      print "Infinity"

    # Define test functions
    (v, q) = TestFunctions(W)

    # Define functions
    state_new = Function(W, name="New_state")  # solution of the next timestep 
    state_nl = Function(W, name="Best_guess_state")  # the last computed state of the next timestep, used for the picard iteration

    # Split mixed functions
    if is_nonlinear and newton_solver:
      u, h = split(state_new) 
    else:
      (u, h) = TrialFunctions(W) 
    u0, h0 = split(state)
    u_nl, h_nl = split(state_nl)

    # Create initial conditions and interpolate
    state_new.assign(state, annotate=annotate)

    # u_(n+theta) and h_(n+theta)
    u_mid = (1.0-theta)*u0 + theta*u
    h_mid = (1.0-theta)*h0 + theta*h

    # If a picard iteration is used we need an intermediate state 
    if is_nonlinear and not newton_solver:
      u_nl, h_nl = split(state_nl)
      state_nl.assign(state, annotate=annotate)
      u_mid_nl = (1.0-theta)*u0 + theta*u_nl

    # The normal direction
    n = FacetNormal(W.mesh())

    # Mass matrix
    M = inner(v, u) * dx
    M += inner(q, h) * dx
    M0 = inner(v, u0) * dx
    M0 += inner(q, h0) * dx

    # Divergence term.
    Ct_mid = -inner(u_mid, grad(q))*dx
    #+inner(avg(u_mid),jump(q,n))*dS # This term is only needed for dg element pairs

    if params["bctype"] == 'dirichlet':
      # The dirichlet boundary condition on the left hand side 
      ufl = Expression(("eta0*sqrt(g*depth)*cos(k*x[0]-sqrt(g*depth)*k*t)", "0", "0"), eta0=params["eta0"], g=g, depth=depth, t=t, k=params["k"])
      bc_contr = -dot(ufl, n) * q * ds(1)
      #bc_contr = -dot(u_mid, n) * q * ds(1)

      # The dirichlet boundary condition on the right hand side
      bc_contr -= dot(ufl, n) * q * ds(2)
      #bc_contr -= dot(u_mid, n) * q * ds(2)

      # We enforce a no-normal flow on the sides by removing the surface integral. 
      # bc_contr -= dot(u_mid, n) * q * ds(3)

    elif params["bctype"] == 'flather':
      # The Flather boundary condition on the left hand side 
      ufl = Expression(("2*eta0*sqrt(g*depth)*cos(-sqrt(g*depth)*k*t)", "0", "0"), eta0=params["eta0"], g=g, depth=depth, t=t, k=params["k"])
      bc_contr = -dot(ufl, n) * q * ds(1)
      Ct_mid += sqrt(g*depth)*inner(h_mid, q)*ds(1)

      # The contributions of the Flather boundary condition on the right hand side
      Ct_mid += sqrt(g*depth)*inner(h_mid, q)*ds(2)
    else:
      print "Unknown boundary condition type"
      sys.exit(1)

    # Pressure gradient operator
    C_mid = (g * depth) * inner(v, grad(h_mid)) * dx
    #+inner(avg(v),jump(h_mid,n))*dS # This term is only needed for dg element pairs

    # Bottom friction
    class FrictionExpr(Expression):
        def eval(self, value, x):
           value[0] = params["friction"] 

    friction = FrictionExpr()
    if turbine_field:
      friction += turbine_field

    # Friction term
    # With a newton solver we can simply use a non-linear form
    if quadratic_friction and newton_solver:
      R_mid = g * friction**2 / (depth**(4./3)) * dot(u_mid, u_mid)**0.5 * inner(u_mid, v) * dx 
    # With a picard iteration we need to linearise using the best guess
    elif quadratic_friction and not newton_solver:
      R_mid = g * friction**2 / (depth**(4./3)) * dot(u_mid_nl, u_mid_nl)**0.5 * inner(u_mid, v) * dx 
    # Use a linear drag
    else:
      R_mid = g * friction**2 / (depth**(1./3)) * inner(u_mid, v) * dx 

    # Advection term 
    # With a newton solver we can simply use a quadratic form
    if include_advection and newton_solver:
      Ad_mid = 1/depth * inner(grad(u_mid)*u_mid, v)*dx
    # With a picard iteration we need to linearise using the best guess
    if include_advection and not newton_solver:
      Ad_mid = 1/depth * inner(grad(u_mid)*u_mid_nl, v)*dx

    if include_diffusion:
      # Check that we are not using a DG velocity function space, as the facet integrals are not implemented.
      if "Discontinuous" in str(W.split()[0]):
        raise NotImplementedError, "The diffusion term for discontinuous elements is not implemented yet."
      D_mid = diffusion_coef*inner(grad(u_mid), grad(v))*dx

    # Create the final form
    G_mid = C_mid + Ct_mid + R_mid 
    # Add the advection term
    if include_advection:
      G_mid += Ad_mid
    # Add the diffusion term
    if include_diffusion:
      G_mid += D_mid
    # Add the source term
    if u_source:
      G_mid -= inner(u_source, v)*dx 
    F = M - M0 + dt * G_mid - dt * bc_contr

    # Preassemble the lhs if possible
    use_lu_solver = (linear_solver == "lu") 
    if not quadratic_friction and not include_advection:
        lhs_preass = assemble(dolfin.lhs(F))
        # Precompute the LU factorisation 
        if use_lu_solver:
          info_green("Computing the LU factorisation for later use ...")
          lu_solver = LUSolver(lhs_preass)
          lu_solver.parameters["reuse_factorization"] = True

    ############################### Perform the simulation ###########################

    u_out, p_out = output_files(params["basename"])
    M_u_out, v_out, u_out_state = u_output_projector(state.function_space())
    M_p_out, q_out, p_out_state = p_output_projector(state.function_space())

    # Project the solution to P1 for visualisation.
    rhs = assemble(inner(v_out, state.split()[0])*dx)
    solve(M_u_out, u_out_state.vector(), rhs, "cg", "sor", annotate=False) 
    rhs = assemble(inner(q_out, state.split()[1])*dx)
    solve(M_p_out, p_out_state.vector(), rhs, "cg", "sor", annotate=False) 
    u_out << u_out_state
    p_out << p_out_state
    
    step = 0    

    if time_functional is not None:
      quad = 0.5
      j =  dt * quad * assemble(time_functional.Jt(state)) 
      djdm = dt * quad * numpy.array([assemble(f) for f in time_functional.dJtdm(state)])

    while (t < params["finish_time"]):
        t += dt
        params["current_time"] = t

        ufl.t=t-(1.0-theta)*dt # Update time for the Boundary condition expression
        if u_source:
          u_source.t = t-(1.0-theta)*dt  # Update time for the source term
        step+=1

        ## Set parameters for the solvers. Note: These options are ignored if use_lu_solver == True
        if solver_benchmark:
          linear_solver_set = [linear_solver, "lu", "gmres", "bicgstab", "minres", "tfqmr", "richardson"]
          preconditioner_set =[preconditioner, "none", "ilu", "icc", "jacobi", "bjacobi", "sor", "amg", "additive_schwarz", "hypre_amg", "hypre_euclid", "hypre_parasails"]
          solver_parameters_set = []
          for l in linear_solver_set:
            for p in preconditioner_set:
              if l == "lu" and p != "none":
                continue
              if l == "default" and p != "none":
                continue
              if l == "cholesky" and p != "none":
                continue
              if l == "gmres" and (p == "none" or p == "default"):
                continue
              solver_parameters_set.append({"linear_solver": l, "preconditioner": p})

          solver_benchmark_results = {}
          info_green("Starting solver benchmark...")

        else:
          solver_parameters_set = [{"linear_solver": linear_solver, "preconditioner": preconditioner}]
        
        # Solve the problem with the chosen parameters
        for solver_parameters in solver_parameters_set:
          if solver_benchmark:
            solver_failed = False
            timer = Timer("Solver benchmark")
            timer.start()

          # Solve non-linear system with a Newton sovler
          if is_nonlinear and newton_solver:
            # Use a Newton solver to solve the nonlinear problem.
            solver_parameters["newton_solver"] = {}
            solver_parameters["newton_solver"]["convergence_criterion"] = "incremental"
            solver_parameters["newton_solver"]["relative_tolerance"] = 1e-16
            solve(F == 0, state_new, solver_parameters=solver_parameters, annotate=annotate)

          # Solve non-linear system with a Picard iteration
          elif is_nonlinear:
            # Solve the problem using a picard iteration
            for i in range(picard_iterations):
              solve(dolfin.lhs(F) == dolfin.rhs(F), state_new, solver_parameters=solver_parameters, annotate=annotate)
              if i > 0:
                diff = abs(assemble( inner(state_new-state_nl, state_new-state_nl) * dx ))
                dolfin.info_blue("Picard iteration difference at iteration " + str(i+1) + " is " + str(diff) + ".")
              state_nl.assign(state_new)

          # Solve linear system with preassembled matrices 
          else:
              rhs_preass = assemble(dolfin.rhs(F))
              if use_lu_solver:
                info_green("Using a LU solver to solve the linear system.")
                lu_solver.solve(state.vector(), rhs_preass, annotate=annotate)
              else:
                try:
                  state_tmp = Function(state.function_space(), name="TempState")
                  solve(lhs_preass, state_new.vector(), rhs_preass, solver_parameters["linear_solver"], solver_parameters["preconditioner"], annotate=annotate)
                except RuntimeError as e:
                  if solver_benchmark:
                    solver_failed = True
                    pass
                  else:
                    raise RuntimeError, e.message

          if solver_benchmark:
            timer.stop()
            if solver_failed:
              info_red(solver_parameters["linear_solver"] + ", " + solver_parameters["preconditioner"] + ": Solver failed")
            else:
              info_green(solver_parameters["linear_solver"] + ", " + solver_parameters["preconditioner"] + ": " + str(timer.value()) + "s")
            if not solver_failed:
              solver_benchmark_results[timer.value()] = solver_parameters 

        # After the timestep solve, update state
        state.assign(state_new)

        # Let's analyse the result of the benchmark test:
        if solver_benchmark:
          info_green("\n **** Benchmark results for timestep " + str(step) + " *** \n")
          def sortDict(dict):
             keys = dict.keys()
             keys.sort()
             return keys, [dict[key] for key in keys]

          times, solvers = sortDict(solver_benchmark_results)
          for i in range(len(times)):
            print "%s, %s: %.2f s" % (str(solvers[i]['linear_solver']), str(solvers[i]['preconditioner']), times[i])

        if step%params["dump_period"] == 0:
        
            # Project the solution to P1 for visualisation.
            rhs=assemble(inner(v_out,state.split()[0])*dx)
            solve(M_u_out, u_out_state.vector(), rhs, "cg", "sor", annotate=False) 
            rhs=assemble(inner(q_out,state.split()[1])*dx)
            solve(M_p_out, p_out_state.vector(), rhs, "cg", "sor", annotate=False) 
            
            u_out << u_out_state
            p_out << p_out_state

        if time_functional is not None:
          if t >= params["finish_time"]:
            quad = 0.5
          else:
            quad = 1.0
          j += dt * quad * assemble(time_functional.Jt(state)) 
          djtdm = numpy.array([assemble(f) for f in time_functional.dJtdm(state)])
          djdm += dt * quad * djtdm

        # Increase the adjoint timestep
        adj_inc_timestep()

    if time_functional is not None:
      return j, djdm # return the state at the final time

def replay(params):

    myid = MPI.process_number()
    if myid == 0 and params["verbose"] > 0:
      print "Replaying forward run"

    for i in range(adjointer.equation_count):
        (fwd_var, output) = adjointer.get_forward_solution(i)

        s=libadjoint.MemoryStorage(output)
        s.set_compare(0.0)
        s.set_overwrite(True)

        adjointer.record_variable(fwd_var, s)

def adjoint(state, params, functional, until=None):
    ''' Runs the adjoint model with the provided functional and returns the adjoint solution 
        of the last adjoint solve. The optional until parameter must be an integer that specified,
        up to which equation the adjoint model is to be solved.''' 

    myid = MPI.process_number()
    if myid == 0 and params["verbose"] > 0:
      print "Running adjoint"

    for i in range(adjointer.equation_count)[::-1]:
        if myid == 0 and params["verbose"] > 2:
          print "  solving adjoint equation ", i
        (adj_var, output) = adjointer.get_adjoint_solution(i, functional)
        if until != None:
          if adj_var.name == until["name"] and adj_var.timestep == until["timestep"] and adj_var.iteration == until["iteration"]:
            break

        s=libadjoint.MemoryStorage(output)
        adjointer.record_variable(adj_var, s)

    return output.data # return the adjoint solution associated with the initial condition


def u_output_projector(W):
    # Projection operator for output.
    Output_V=VectorFunctionSpace(W.mesh(), 'CG', 1, dim=2)
    
    u_out=TrialFunction(Output_V)
    v_out=TestFunction(Output_V)
    
    M_out=assemble(inner(v_out,u_out)*dx)
    
    out_state=Function(Output_V)

    return M_out, v_out, out_state

def p_output_projector(W):
    # Projection operator for output.
    Output_V=FunctionSpace(W.mesh(), 'CG', 1)
    
    u_out=TrialFunction(Output_V)
    v_out=TestFunction(Output_V)
    
    M_out=assemble(inner(v_out,u_out)*dx)
    
    out_state=Function(Output_V)

    return M_out, v_out, out_state

def output_files(basename):
        
    # Output file
    u_out = File(basename+"_u.pvd", "compressed")
    p_out = File(basename+"_p.pvd", "compressed")

    return u_out, p_out
            


from dolfin import *
from dolfin_adjoint import *
import solver_benchmark 
import libadjoint
import numpy
import sys

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

    # To begin with, check if the provided parameters are valid
    params.check()

    theta = params["theta"]
    dt = params["dt"]
    g = params["g"]
    depth = params["depth"]
    # Reset the time
    params["current_time"] = params["start_time"]
    t = params["current_time"]
    quadratic_friction = params["quadratic_friction"]
    include_advection = params["include_advection"]
    include_diffusion = params["include_diffusion"]
    diffusion_coef = params["diffusion_coef"]
    newton_solver = params["newton_solver"] 
    picard_relative_tolerance = params["picard_relative_tolerance"]
    picard_iterations = params["picard_iterations"]
    run_benchmark = params["run_benchmark"]
    solver_exclude = params["solver_exclude"]
    is_nonlinear = (include_advection or quadratic_friction)

    # Print out an estimation of the Reynolds number 
    if include_diffusion and diffusion_coef>0:
      reynolds = params["turbine_x"]*2./diffusion_coef
    else:
      reynolds = "oo"
    info("Expected Reynolds number is roughly (assumes velocity is 2): %s" % str(reynolds))

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
      info_red("Unknown boundary condition type")
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
          info("Computing the LU factorisation for later use ...")
          lu_solver = LUSolver(lhs_preass)
          lu_solver.parameters["reuse_factorization"] = True

    solver_parameters = {"linear_solver": linear_solver, "preconditioner": preconditioner}

    ############################### Perform the simulation ###########################

    u_out, p_out = output_files(params["element_type"].func_name)
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

        
        # Solve non-linear system with a Newton sovler
        if is_nonlinear and newton_solver:
          # Use a Newton solver to solve the nonlinear problem.
          solver_parameters["newton_solver"] = {}
          solver_parameters["newton_solver"]["convergence_criterion"] = "incremental"
          solver_parameters["newton_solver"]["relative_tolerance"] = 1e-16
          solver_benchmark.solve(F == 0, state_new, solver_parameters = solver_parameters, annotate=annotate, benchmark = run_benchmark, solve = solve, solver_exclude = solver_exclude)

        # Solve non-linear system with a Picard iteration
        elif is_nonlinear:
          # Solve the problem using a picard iteration
          iter_counter = 0
          while True:
            solver_benchmark.solve(dolfin.lhs(F) == dolfin.rhs(F), state_new, solver_parameters = solver_parameters, annotate=annotate, benchmark = run_benchmark, solve = solve, solver_exclude = solver_exclude)
            iter_counter += 1
            if iter_counter > 0:
              relative_diff = abs(assemble( inner(state_new-state_nl, state_new-state_nl) * dx ))/norm(state_new)

              if relative_diff < picard_relative_tolerance:
                dolfin.info("Picard iteration converged after " + str(iter_counter) + " iterations.")
                break
              elif iter_counter >= picard_iterations:
                dolfin.info_red("Picard iteration reached maximum number of iterations (" + str(picard_iterations) + ") with a relative difference of " + str(relative_diff) + ".")
                break

            state_nl.assign(state_new)

        # Solve linear system with preassembled matrices 
        else:
            rhs_preass = assemble(dolfin.rhs(F))
            if use_lu_solver:
              info("Using a LU solver to solve the linear system.")
              lu_solver.solve(state.vector(), rhs_preass, annotate=annotate)
            else:
              state_tmp = Function(state.function_space(), name="TempState")
              solver_benchmark.solve(lhs_preass, state_new.vector(), rhs_preass, solver_parameters["linear_solver"], solver_parameters["preconditioner"], annotate=annotate, benchmark = run_benchmark, solve = solve, solver_exclude = solver_exclude)

        # After the timestep solve, update state
        state.assign(state_new)

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
      info("Replaying forward run")

    for i in range(adjointer.equation_count):
        (fwd_var, output) = adjointer.get_forward_solution(i)

        s = libadjoint.MemoryStorage(output)
        s.set_compare(0.0)
        s.set_overwrite(True)

        adjointer.record_variable(fwd_var, s)

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
            

